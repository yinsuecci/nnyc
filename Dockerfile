FROM python:3.12-slim
WORKDIR /opt/nn-remote
COPY stun_binding.py ./
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY signal_server.py health_probe.py relay.py cluster.py relay_worker.py server_metrics.py ./
COPY admin_web ./admin_web
RUN mkdir -p /var/lib/nn-remote && chown 10001:10001 /var/lib/nn-remote
USER 10001:10001
EXPOSE 8443/tcp
ENTRYPOINT ["python", "-B", "signal_server.py", "--config", "/run/nn-remote/config.json"]
