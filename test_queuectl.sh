#!/usr/bin/env bash
set -e

DB_PATH="./test_queue.db"
export QUEUECTL_DB_PATH="$DB_PATH"
QCTL="./queuectl"

cleanup() {
    echo "Cleaning up..."
    $QCTL worker stop >/dev/null 2>&1 || true
    kill $(jobs -p) 2>/dev/null || true
    rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm" "$DB_PATH-wal" "$DB_PATH-shm"
}
trap cleanup EXIT

cleanup

echo "=== Scenario 1: A basic job completes ==="
$QCTL enqueue '{"id":"job-basic","command":"echo success"}'
$QCTL worker start --count 1 &
WORKER_PID=$!
sleep 2 # let worker process it
$QCTL worker stop
wait $WORKER_PID || true

STATUS=$($QCTL list --state completed --json | grep '"id": "job-basic"')
if [ -z "$STATUS" ]; then
    echo "FAIL: Scenario 1 - Basic job did not complete"
    exit 1
fi
echo "PASS: Scenario 1"

echo "=== Scenario 2: Failing job retries and lands in DLQ ==="
$QCTL config set max-retries 1
$QCTL config set backoff-base 1
$QCTL enqueue '{"id":"job-fail","command":"exit 1"}'

$QCTL worker start --count 1 &
WORKER_PID2=$!
sleep 4 # enough time for failure, backoff (1s), and second failure
$QCTL worker stop
wait $WORKER_PID2 || true

STATUS=$($QCTL dlq list --json | grep '"id": "job-fail"')
if [ -z "$STATUS" ]; then
    echo "FAIL: Scenario 2 - Job did not land in DLQ"
    exit 1
fi
echo "PASS: Scenario 2"

echo "=== Scenario 3: Many jobs across multiple workers ==="
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm" "$DB_PATH-wal" "$DB_PATH-shm" # reset DB
for i in {1..20}; do
    $QCTL enqueue "{\"id\":\"job-many-$i\",\"command\":\"sleep 0.1\"}" >/dev/null
done

$QCTL worker start --count 3 &
WORKER_PID3=$!
sleep 4 # wait for 20 quick jobs to process across 3 workers
$QCTL worker stop
wait $WORKER_PID3 || true

COMPLETED_COUNT=$($QCTL list --state completed --json | grep '"id":' | wc -l | xargs)
if [ "$COMPLETED_COUNT" != "20" ]; then
    echo "FAIL: Scenario 3 - Expected 20 completed jobs, got $COMPLETED_COUNT"
    exit 1
fi
echo "PASS: Scenario 3"

echo "=== Scenario 4: Worker is SIGKILLed mid-job and job recovers ==="
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
$QCTL config set max-retries 3
$QCTL config set backoff-base 1

$QCTL enqueue '{"id":"job-kill","command":"sleep 10"}'
$QCTL worker start --count 1 &
WORKER_PID4=$!

sleep 2 # Let it claim the job
# Verify job is processing
IS_PROCESSING=$($QCTL list --state processing --json | grep '"id": "job-kill"')
if [ -z "$IS_PROCESSING" ]; then
    echo "FAIL: Scenario 4 - Job didn't enter processing state"
    exit 1
fi

# Simulate hard crash
echo "Sending SIGKILL to worker $WORKER_PID4..."
kill -9 $WORKER_PID4

# The worker died leaving the job locked.
# Start a new worker. We need to wait for 45s (stale heartbeat timeout)
echo "Starting recovery worker and waiting for 50s for stale timeout..."
$QCTL worker start --count 1 &
WORKER_PID5=$!

sleep 50
$QCTL worker stop
wait $WORKER_PID5 || true

# Wait, the job might have completed by now or failed and retried.
# If it recovered, it ran "sleep 10" again. 50s is enough to recover (45s) and complete (10s) = 55s. Let's sleep 60s total.
sleep 10

IS_COMPLETED=$($QCTL list --state completed --json | grep '"id": "job-kill"')
if [ -z "$IS_COMPLETED" ]; then
    echo "FAIL: Scenario 4 - Job did not recover and complete"
    exit 1
fi
echo "PASS: Scenario 4"

echo "=== Scenario 5: Jobs survive a full restart ==="
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
$QCTL enqueue '{"id":"job-persist","command":"echo persisted"}'

# "Restart" by simply doing nothing and seeing it in the DB (since no processes are running)
IS_PENDING=$($QCTL list --state pending --json | grep '"id": "job-persist"')
if [ -z "$IS_PENDING" ]; then
    echo "FAIL: Scenario 5 - Job didn't persist"
    exit 1
fi

$QCTL worker start --count 1 &
WORKER_PID6=$!
sleep 2
$QCTL worker stop
wait $WORKER_PID6 || true

IS_COMPLETED=$($QCTL list --state completed --json | grep '"id": "job-persist"')
if [ -z "$IS_COMPLETED" ]; then
    echo "FAIL: Scenario 5 - Persisted job didn't complete"
    exit 1
fi
echo "PASS: Scenario 5"

echo "=== Scenario 6: Job Timeout ==="
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
$QCTL config set max-retries 0
$QCTL config set backoff-base 1
$QCTL config set job-timeout 2

$QCTL enqueue '{"id":"job-timeout","command":"sleep 10"}'
$QCTL worker start --count 1 &
WORKER_PID7=$!
sleep 5 # Wait for 2s timeout and failure processing
$QCTL worker stop
wait $WORKER_PID7 || true

STATUS=$($QCTL list --state dead --json | grep '"id": "job-timeout"')
if [ -z "$STATUS" ]; then
    echo "FAIL: Scenario 6 - Job did not timeout and enter dead state"
    exit 1
fi
echo "PASS: Scenario 6"

echo "All scenarios passed successfully!"
