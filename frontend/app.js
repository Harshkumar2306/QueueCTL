// CHANGE THIS TO YOUR RENDER URL BEFORE DEPLOYING TO VERCEL (e.g. 'https://queuectl.onrender.com/api')
// If running locally, you can use 'http://localhost:8080/api'
const API_BASE = '/api'; // fallback for local dashboard

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
            <td><span class="badge dead">${job.attempts} fails</span></td>
            <td>
                <button class="retry-btn" onclick="retryJob('${job.id}')">Retry</button>
            </td>
        </tr>
    `).join('');
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

    } catch (err) {
        console.error('Dashboard fetch error', err);
    }
}

// Initial fetch
fetchData();

// Poll every 2 seconds
setInterval(fetchData, 2000);
