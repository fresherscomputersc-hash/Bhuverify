set -e
cd /home/ubuntu/Bhuverify
cp -n data/bhuverify.db data/bhusure.db 2>/dev/null || cp data/bhuverify.db data/bhusure.db
ls -la data/*.db
sudo tee /etc/systemd/system/bhusure.service > /dev/null <<'EOF'
[Unit]
Description=BhuSure FastAPI Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Bhuverify
Environment="PATH=/home/ubuntu/Bhuverify/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="OMP_THREAD_LIMIT=1"
ExecStart=/home/ubuntu/Bhuverify/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
echo unit-ready
