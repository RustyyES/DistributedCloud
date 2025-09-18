import sys
import json
import asyncio
from common.models import Job
from worker.docker_executor import DockerExecutor

def execute_job():
    """
    Entry point for executing a job.
    Expected usage: python3 -m worker.execute_job '<json_job_payload>'
    """
    if len(sys.argv) < 2:
        print("Usage: python3 -m worker.execute_job <json_job_payload>", file=sys.stderr)
        sys.exit(1)

    try:
        job_data = json.loads(sys.argv[1])
        job = Job(**job_data)
        
        executor = DockerExecutor()
        result = executor.run_job(job)
        
        # Print result as JSON to stdout for the caller (Master via SSH) to capture
        print(result.json())
        sys.exit(result.exit_code)
        
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    execute_job()
