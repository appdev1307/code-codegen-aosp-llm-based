import time
import traceback

from pipeline import run_pipeline

def log(msg):
    print(f"[DEBUG] {msg}", flush=True)

def main():
    log("=== AAOS AI Code Generator + Validator ===")
    start = time.time()

    try:
        log("Starting pipeline...")
        run_pipeline()
        log("Pipeline finished successfully")

    except Exception as e:
        log("EXCEPTION OCCURRED")
        traceback.print_exc()

    finally:
        elapsed = time.time() - start
        log(f"Total execution time: {elapsed:.2f}s")

if __name__ == "__main__":
    main()
