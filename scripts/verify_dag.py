import requests
import time
import sys

BASE_URL = "http://localhost:8000/api/jobs"

def submit_job(name, cmd, deps=[], priority=0):
    payload = {
        "name": name,
        "command": cmd,
        "resource_requirements": {
            "cpu_cores": 1,
            "memory_mb": 128,
            "docker_image": "python:3.9-slim",
            "timeout": 30
        },
        "priority": priority,
        "dependencies": deps
    }
    try:
        r = requests.post(BASE_URL, json=payload)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Failed to submit {name}: {e}")
        print(r.text)
        sys.exit(1)

def get_job(job_id):
    r = requests.get(f"{BASE_URL}/{job_id}")
    return r.json()

def main():
    print("Submitting Job A (Sleep 5s)...")
    job_a = submit_job("Job A", "python -c 'import time; time.sleep(5); print(\"Job A Done\")'")
    print(f"Job A ID: {job_a['id']}")

    print("Submitting Job B (Depends on A)...")
    job_b = submit_job("Job B", "echo 'Job B running after A'", deps=[job_a['id']])
    print(f"Job B ID: {job_b['id']}")

    # Monitor
    start = time.time()
    while True:
        status_a = get_job(job_a['id'])['status']
        status_b = get_job(job_b['id'])['status']
        
        print(f"T+{int(time.time()-start)}s | Job A: {status_a} | Job B: {status_b}")
        
        if status_b == 'completed':
            print("SUCCESS: Job B completed after Job A.")
            break
            
        if status_a == 'failed' or status_b == 'failed':
            print("FAILURE: One of the jobs failed.")
            sys.exit(1)
            
        if time.time() - start > 20:
             print("TIMEOUT: Jobs took too long.")
             sys.exit(1)
             
        time.sleep(1)

if __name__ == "__main__":
    main()

