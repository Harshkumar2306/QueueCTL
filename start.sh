#!/bin/bash

# Initialize the config for the demo environment
./queuectl config set max-retries 2
./queuectl config set backoff-base 2

# Start 2 workers in the background
echo "Starting workers..."
./queuectl worker start --count 2 &

# Start the web dashboard on the port provided by the host (e.g. Render/Railway)
PORT=${PORT:-8080}
echo "Starting dashboard on port $PORT..."
./queuectl dashboard --port $PORT
