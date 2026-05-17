# Deploy replica13f on the VPS (Hedge Fund Filings)

This app runs as **Streamlit on port 8502**. The hub tile on port **5000** links to it.

| Item | Value |
|------|--------|
| VPS IP | `138.197.38.55` (adjust if yours changed) |
| Hub (Agents) | `http://138.197.38.55:5000/` |
| This app | `http://138.197.38.55:8502/` |
| Hub repo on VPS | `/root/dashboard/Agents` |
| App repo on VPS | `/root/dashboard/replica13f` |

---

## Part A — One-time setup on the VPS

### 1. SSH into the VPS

From your Mac:

```bash
ssh -i ~/.ssh/github_ed25519 root@138.197.38.55
```

Use your actual SSH key if it is not `github_ed25519`.

### 2. Clone replica13f

```bash
cd /root/dashboard
git clone https://github.com/dickgibbons/replica13f.git
cd replica13f
```

If `git clone` asks for a password, use a GitHub **Personal Access Token** (not your Gmail password).

### 3. Create a virtualenv and install dependencies

```bash
cd /root/dashboard/replica13f
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create environment file (required for SEC)

```bash
nano /root/dashboard/replica13f/.env
```

Add (use your real name and email — SEC requires this):

```bash
EDGAR_UA=YourName you@yourdomain.com
OPENFIGI_KEY=your_openfigi_key_here
```

Save (`Ctrl+O`, Enter, `Ctrl+X`).  
`OPENFIGI_KEY` is optional for the seed universe but recommended when you add more funds. Get a free key at [OpenFIGI](https://www.openfigi.com/api).

### 5. Smoke-test manually (optional)

```bash
cd /root/dashboard/replica13f
source .venv/bin/activate
set -a && source .env && set +a
streamlit run app.py --server.port=8502 --server.address=0.0.0.0
```

Open `http://138.197.38.55:8502/` in your browser. Press `Ctrl+C` on the VPS when done.

### 6. Open firewall port 8502

```bash
ufw allow 8502/tcp
ufw status
```

If you use DigitalOcean Cloud Firewall instead of `ufw`, add an inbound rule for **TCP 8502** in the DO control panel.

### 7. Install systemd service (starts on boot)

```bash
cp /root/dashboard/replica13f/scripts/systemd/replica13f.service.example \
   /etc/systemd/system/replica13f.service

systemctl daemon-reload
systemctl enable replica13f
systemctl start replica13f
systemctl status replica13f
```

You want `Active: active (running)`. Logs:

```bash
journalctl -u replica13f -f
```

---

## Part B — Hub tile (“Hedge Fund Filings” on the main screen)

The tile lives in the **Agents** repo (`frontend/index.html`), not in replica13f.

### 8. On your Mac — commit and push Agents (if not already done)

```bash
cd "/Users/dickgibbons/Documents/AI Projects/Agents"
git status
git add frontend/index.html frontend/investing.html
git commit -m "Add Hedge Fund Filings hub tile (port 8502)"
git push origin main
```

### 9. On the VPS — pull Agents and restart the hub

```bash
cd /root/dashboard/Agents
git pull origin main
systemctl restart multi-llm-server
systemctl status multi-llm-server
```

### 10. Verify in the browser

1. Open **http://138.197.38.55:5000/** — you should see **Hedge Fund Filings**.
2. Click it — should open **http://138.197.38.55:8502/** (Streamlit).
3. In the app: set methodology in the sidebar → **Load holdings** / **Load moves** / **Run ranking**.

---

## Part C — Routine updates

### Update replica13f only

```bash
ssh -i ~/.ssh/github_ed25519 root@138.197.38.55
cd /root/dashboard/replica13f
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart replica13f
```

### Update hub UI only

```bash
cd /root/dashboard/Agents
git pull origin main
systemctl restart multi-llm-server
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Hub tile missing | Agents not pulled/restarted; check `frontend/index.html` on VPS |
| `:8502` connection refused | `systemctl status replica13f`; check firewall / DO rules |
| SEC errors / empty filings | Set `EDGAR_UA` in `.env` to `Name email@domain.com` and restart service |
| Slow first load | Normal — EDGAR + prices cache under `cache/`; reruns are faster |
| `streamlit: command not found` in systemd | Use full path: `/root/dashboard/replica13f/.venv/bin/streamlit` in the unit file |

### Useful commands

```bash
systemctl restart replica13f
journalctl -u replica13f -n 50 --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8502/
```

---

## Local development (Mac)

Same app, no systemd:

```bash
cd "/Users/dickgibbons/Documents/Documents - Dick’s MacBook Pro/GitHub/replica13f"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export EDGAR_UA="YourName you@domain.com"
export OPENFIGI_KEY=...
streamlit run app.py
```

Opens at **http://localhost:8501** by default (Streamlit’s default port when 8502 is free).
