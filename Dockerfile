FROM python:3.10-slim

WORKDIR /app

# Copy the entire project
COPY . /app

# Ensure scripts are executable
RUN chmod +x /app/queuectl /app/demo.sh

# Run the demo script which spins up workers, a job generator, and the dashboard
CMD ["/app/demo.sh"]
