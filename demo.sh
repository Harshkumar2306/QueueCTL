#!/bin/bash

# Initialize the config for the demo environment
./queuectl config set max-retries 2
./queuectl config set backoff-base 2

# Start 2 workers in the background
echo "Starting workers..."
./queuectl worker start --count 2 &

# Start a background loop to continuously feed jobs into the queue
echo "Starting job generator..."
(
while true; do
    # 80% chance of a successful job (sleeps for 1-3 seconds)
    # 20% chance of a failing job to populate the DLQ
    if [ $((RANDOM % 5)) -eq 0 ]; then
        ./queuectl enqueue "{\"id\":\"job-fail-$(date +%s)\",\"command\":\"echo 'Simulating failure' && exit 1\"}" >/dev/null
    else
        SLEEP_TIME=$(( (RANDOM % 3) + 1 ))
        ./queuectl enqueue "{\"id\":\"job-pass-$(date +%s)\",\"command\":\"sleep $SLEEP_TIME && echo 'Success'\"}" >/dev/null
    fi
    # Wait a bit before adding the next job
    sleep 2
done
) &

# Start the web dashboard on the port provided by the host (e.g. Render/Railway)
PORT=${PORT:-8080}
echo "Starting dashboard on port $PORT..."
./queuectl dashboard --port $PORT
