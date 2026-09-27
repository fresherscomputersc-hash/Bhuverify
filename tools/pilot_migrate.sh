set -e
cd /home/ubuntu/BhuSure-pilot
TMPDIR=/home/ubuntu/tmp ./venv-pilot/bin/python -m spacy download en_core_web_sm 2>&1 | tail -1
mkdir -p data/uploads data/processed data/geojson data/reports data/samples
cp /home/ubuntu/Bhuverify-pilot/data/bhuverify.db data/bhusure.db
for d in uploads processed geojson reports; do
  cp -rn /home/ubuntu/Bhuverify-pilot/data/$d/. data/$d/ 2>/dev/null || true
done
cp /home/ubuntu/Bhuverify-pilot/.env.pilot .env.pilot
ls -la data/*.db .env.pilot
sudo tee /etc/systemd/system/bhusure-pilot.service > /dev/null <<'EOF'
[Unit]
Description=BhuSure Pilot - spaCy+HF verifiers
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/BhuSure-pilot
Environment=PATH=/home/ubuntu/BhuSure-pilot/venv-pilot/bin:/usr/local/bin:/usr/bin:/bin
Environment=OMP_THREAD_LIMIT=1
Environment=TMPDIR=/home/ubuntu/tmp
EnvironmentFile=/home/ubuntu/BhuSure-pilot/.env.pilot
ExecStart=/home/ubuntu/BhuSure-pilot/venv-pilot/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bhusure-pilot
sleep 50
sudo systemctl is-active bhusure-pilot
curl -s -o /dev/null -w 'pilot-new:%{http_code}\n' http://127.0.0.1:8001/api/v1/health
