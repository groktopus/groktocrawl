FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY common/capture_destination.py /app/common/capture_destination.py
COPY capture-egress-svc/capture_egress_svc /app/capture_egress_svc

EXPOSE 8080
CMD ["python", "-m", "capture_egress_svc"]
