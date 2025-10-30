import queue
import threading
import time
import json
from typing import Dict, List, Optional
from common.models import Job, JobStatus, Node, NodeStatus, JobResult
from master.cluster_manager import ClusterManager
from master.load_balancer import LoadBalancer
from common.ssh_client import SSHClient

class JobScheduler:
    def __init__(self, cluster_manager: ClusterManager, metrics_server=None):
        self.cluster_manager = cluster_manager
        self.job_queue = queue.PriorityQueue()
        self.jobs: Dict[str, Job] = {}
        self.running = False
        self.lock = threading.Lock()
        self.load_balancer = LoadBalancer()
        self.metrics_server = metrics_server
        self.ssh_pool = {} # Map node_id -> SSHClient instance
        self.pool_lock = threading.Lock()

    def _get_ssh_client(self, node: Node) -> SSHClient:
        with self.pool_lock:
            if node.id in self.ssh_pool:
                client = self.ssh_pool[node.id]
                if client.client.get_transport() and client.client.get_transport().is_active():
                    return client
                else:
                    # Stale connection
                     del self.ssh_pool[node.id]
            
            # Create new
            client = SSHClient(
                host=node.ip_address,
                user=node.ssh_user,
                port=node.ssh_port,
                key_path=f"keys/{node.id}_id_rsa"
            )
            # connect immediately? handled by exec_command but better here to cache
            # The SSHClient wrapper calls connect() lazily or explicitly.
            # We want to cache the connected client.
            # But SSHClient wrapper might not be designed for reuse if not careful.
            # Let's assume it is.
            self.ssh_pool[node.id] = client
            return client

    def submit_job(self, job: Job) -> Job:
        with self.lock:
            job.status = JobStatus.QUEUED
            self.jobs[job.id] = job
            self.job_queue.put((-job.priority, job.submitted_at.timestamp(), job))
            print(f"Job submitted: {job.id}")
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        return list(self.jobs.values())

    def cancel_job(self, job_id: str):
        with self.lock:
            if job_id in self.jobs:
                job = self.jobs[job_id]
                if job.status in [JobStatus.QUEUED, JobStatus.RUNNING]:
                    job.status = JobStatus.CANCELLED
                    print(f"Job cancelled: {job_id}")

    def _schedule_loop(self):
        while self.running:
            try:
                if self.job_queue.empty():
                    time.sleep(1)
                    continue

                priority, timestamp, job = self.job_queue.get(timeout=1)
                
                if job.status != JobStatus.QUEUED:
                    continue

                # Check dependencies
                if job.dependencies:
                    force_push_back = False
                    for dep_id in job.dependencies:
                        dep_job = self.jobs.get(dep_id)
                        if not dep_job or dep_job.status != JobStatus.COMPLETED:
                            force_push_back = True
                            break
                    
                    if force_push_back:
                        self.job_queue.put((priority, timestamp, job))
                        time.sleep(0.5)
                        continue

                node = self._find_node_for_job(job)
                if node:
                    self._assign_job(job, node)
                else:
                    self.job_queue.put((priority, timestamp, job)) 
                    time.sleep(1)

            except queue.Empty:
                self._recover_stranded_jobs()
                continue
            except Exception as e:
                print(f"Scheduler error: {e}")

    def _recover_stranded_jobs(self):
        """Check for jobs running on nodes that are no longer active"""
        # Get set of active node IDs
        active_nodes = {n.id for n in self.cluster_manager.get_active_nodes()}
        
        with self.lock:
            # We iterate a copy of values to avoid modification issues if logic changes
            for job in list(self.jobs.values()):
                if job.status == JobStatus.RUNNING and job.assigned_node:
                    # If assigned node is NOT active (OFFLINE or missing)
                    # Note: get_active_nodes filter returns only ACTIVE. 
                    # If a node missed heartbeat, it won't be in the set.
                    if job.assigned_node not in active_nodes:
                        print(f"Detected stranded job {job.id} on dead node {job.assigned_node}. Re-queueing.")
                        job.status = JobStatus.QUEUED
                        job.assigned_node = None
                        job.retry_count += 1 # Count as a retry? Or separate "recovery"? Let's count it.
                        
                        # Re-queue
                        self.job_queue.put((-job.priority, datetime.utcnow().timestamp(), job))
                        
                        if self.metrics_server:
                             # Maybe track detailed metric here?
                             pass

    def _find_node_for_job(self, job: Job) -> Optional[Node]:
        active_nodes = self.cluster_manager.get_active_nodes()
        return self.load_balancer.select_node(active_nodes, job)

    def _assign_job(self, job: Job, node: Node):
        with self.lock:
            job.status = JobStatus.RUNNING
            job.assigned_node = node.id
            job.started_at = datetime.utcnow()
            
            # Update Node resources locally as a reservation
            if node.resources:
                node.resources.cpu_available -= job.resource_requirements.cpu_cores
                node.resources.memory_available_mb -= job.resource_requirements.memory_mb
            
            print(f"Assigned job {job.id} to node {node.id}")
            
            # Dispatch async
            threading.Thread(target=self._dispatch_to_worker, args=(job, node)).start()

    def _dispatch_to_worker(self, job: Job, node: Node):
        print(f"Dispatching job {job.id} to {node.ip_address}...")
        try:
            # Note: Assuming key-based auth is set up or shared key
            # In a real system, we'd manage keys securely.
            # Here we assume the user running the master can SSH to the worker user.
            
            # Use pooled connection
            ssh = self._get_ssh_client(node)
            
            # Serialize job to JSON for the CLI
            try:
                job_json = job.json()
            except AttributeError:
                 job_json = job.model_dump_json()
            
            # Escape inner quotas for shell? 
            job_json = job_json.replace("'", "'\\''")

            cmd = f"venv/bin/python3 -m worker.execute_job '{job_json}'" # Assuming same venv path on worker for simplicity in Phase 2
            
            # Don't use 'with ssh:' as it might close it? 
            # SSHClient in common/ssh_client.py: __enter__ returns self, __exit__ calls close().
            # So we MUST NOT use context manager if we want to pool.
            
            code, stdout, stderr = ssh.exec_command(cmd, timeout=job.resource_requirements.timeout + 10)
                
            if code == 0:
                # Parse result from stdout (last line?)
                # The CLI prints the result JSON to stdout.
                try:
                    result_data = json.loads(stdout.strip().split('\n')[-1])
                    job.result = JobResult(**result_data)
                    job.status = JobStatus.COMPLETED
                    job.completed_at = datetime.utcnow()
                    print(f"Job {job.id} completed successfully.")
                    if self.metrics_server:
                        self.metrics_server.track_job_completion(job)
                except Exception as e:
                    job.status = JobStatus.FAILED
                    job.result = JobResult(exit_code=1, stdout=stdout, stderr=f"Failed to parse result: {e}\n{stderr}", execution_time_ms=0)
                    if self.metrics_server:
                        self.metrics_server.track_job_failure(job)
            else:
                # Job failed with non-zero exit code
                if job.retry_count < job.max_retries:
                    job.retry_count += 1
                    job.status = JobStatus.QUEUED
                    job.assigned_node = None
                    job.result = None # Clear result
                    print(f"Job {job.id} failed. Retrying ({job.retry_count}/{job.max_retries})...")
                    self.job_queue.put((-job.priority, datetime.utcnow().timestamp(), job))
                else:
                    job.status = JobStatus.FAILED
                    job.result = JobResult(exit_code=code, stdout=stdout, stderr=stderr, execution_time_ms=0)
                    print(f"Job {job.id} failed with exit code {code}. Max retries reached.")
                    if self.metrics_server:
                        self.metrics_server.track_job_failure(job)
                
        except Exception as e:
            print(f"Dispatch failed for job {job.id}: {e}")
            
            # Transport failure (SSH), usually worth a retry if node is transient, 
            # but if we just failed to connect, maybe we should re-queue?
            if job.retry_count < job.max_retries:
                job.retry_count += 1
                job.status = JobStatus.QUEUED
                job.assigned_node = None
                print(f"Job {job.id} dispatch error. Retrying ({job.retry_count}/{job.max_retries})...")
                self.job_queue.put((-job.priority, datetime.utcnow().timestamp(), job))
            else:
                job.status = JobStatus.FAILED
                if self.metrics_server:
                    self.metrics_server.track_job_failure(job)
            # Release resources
            # (In a better design, the periodic resource report would correct this)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self.thread.start()
        print("Job Scheduler started.")

    def stop(self):
        self.running = False
        if hasattr(self, 'thread'):
            self.thread.join()



