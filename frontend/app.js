// CHANGE THIS TO YOUR RENDER URL BEFORE DEPLOYING TO VERCEL (e.g. 'https://queuectl.onrender.com/api')
// If running locally, you can use 'http://localhost:8080/api'
const API_BASE = 'https://queuectl-sl39.onrender.com/api'; // fallback for local dashboard

function updateStat(id, newValue) {
    const el = document.getElementById(id);
    const currentValue = parseInt(el.textContent) || 0;
    if (currentValue !== newValue) {
        el.textContent = newValue;
        el.classList.remove('changed');
        void el.offsetWidth; // trigger reflow
        el.classList.add('changed');
    }
}

function renderWorkers(workers) {
    const tbody = document.querySelector('#workers-table tbody');
    const emptyState = document.getElementById('workers-empty');
    
    if (!workers || workers.length === 0) {
        tbody.innerHTML = '';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    
    // Sort by PID
    workers.sort((a, b) => a.pid - b.pid);
    
    tbody.innerHTML = workers.map(w => {
        // Calculate uptime roughly from started_at string
        const start = new Date(w.started_at + 'Z');
        const now = new Date();
        const diffMs = now - start;
        let uptime = Math.floor(diffMs / 1000) + 's';
        if (diffMs > 60000) uptime = Math.floor(diffMs / 60000) + 'm';
        
        return `
            <tr>
                <td><strong>#${w.pid}</strong></td>
                <td><span class="badge ${w.status}">${w.status}</span></td>
                <td>${uptime}</td>
                <td style="color: var(--text-secondary)">${w.heartbeat_at.split(' ')[1] || w.heartbeat_at}</td>
            </tr>
        `;
    }).join('');
}

function renderDLQ(dlq) {
    const tbody = document.querySelector('#dlq-table tbody');
    const emptyState = document.getElementById('dlq-empty');
    
    if (!dlq || dlq.length === 0) {
        tbody.innerHTML = '';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    
    tbody.innerHTML = dlq.map(job => `
        <tr>
            <td style="font-family: monospace;">${job.id}</td>
            <td style="font-family: monospace; color: var(--text-secondary); max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${job.command.replace(/"/g, '&quot;')}">${job.command}</td>
            <td><span class="badge dead">${job.attempts} fails</span></td>
            <td>
                <button class="retry-btn" onclick="retryJob('${job.id}')">Retry</button>
            </td>
        </tr>
    `).join('');
}

function renderJobs(jobs) {
    const tbody = document.querySelector('#jobs-table tbody');
    const emptyState = document.getElementById('jobs-empty');
    
    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = '';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    
    tbody.innerHTML = jobs.map(job => {
        let statusClass = job.state;
        return `
            <tr>
                <td style="font-family: monospace;">${job.id}</td>
                <td style="font-family: monospace; color: var(--text-secondary);">${job.command}</td>
                <td><span class="badge ${statusClass}">${job.state}</span></td>
                <td style="color: var(--text-secondary)">${job.created_at.split(' ')[1] || job.created_at}</td>
                <td>${job.attempts}</td>
            </tr>
        `;
    }).join('');
}

async function retryJob(jobId) {
    try {
        const res = await fetch(`${API_BASE}/dlq/retry`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: jobId })
        });
        if (res.ok) {
            fetchData(); // refresh immediately
        } else {
            alert('Failed to retry job.');
        }
    } catch (err) {
        console.error('Retry error', err);
    }
}

async function submitJob() {
    const input = document.getElementById('cmd-input');
    const command = input.value.trim();
    if (!command) return;

    try {
        const res = await fetch(`${API_BASE}/enqueue`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: command })
        });
        if (res.ok) {
            input.value = ''; // clear input
            fetchData(); // refresh immediately
        } else {
            alert('Failed to enqueue job.');
        }
    } catch (err) {
        console.error('Enqueue error', err);
    }
}

async function fetchData() {
    try {
        // Fetch stats & workers
        const statsRes = await fetch(`${API_BASE}/stats`);
        const statsData = await statsRes.json();
        
        updateStat('stat-pending', statsData.counts.pending || 0);
        updateStat('stat-processing', statsData.counts.processing || 0);
        updateStat('stat-completed', statsData.counts.completed || 0);
        updateStat('stat-failed', statsData.counts.failed || 0);
        updateStat('stat-dead', statsData.counts.dead || 0);
        
        renderWorkers(statsData.workers);

        // Fetch DLQ
        const dlqRes = await fetch(`${API_BASE}/dlq`);
        const dlqData = await dlqRes.json();
        renderDLQ(dlqData.dlq);

        // Fetch Recent Jobs
        const jobsRes = await fetch(`${API_BASE}/jobs`);
        const jobsData = await jobsRes.json();
        renderJobs(jobsData.jobs);

    } catch (err) {
        console.error('Dashboard fetch error', err);
    }
}

// Initial fetch
fetchData();

// Poll every 2 seconds
setInterval(fetchData, 2000);
