#!/usr/bin/env python3
"""
Teste de carga MQTT com medição de latência, jitter e perda de mensagens.

Metodologia (consistente com a Seção 6.4 do artigo): cada mensagem publicada
carrega um timestamp de envio (T_start). O mesmo processo assina o tópico de
eco/resposta e, ao receber a confirmação, calcula a latência individual
L_i = T_end - T_start (RTT). Jitter é a diferença absoluta entre latências de
transações consecutivas, igual à Eq. 1 e à definição de jitter do artigo.

Este script publica em uma taxa fixa (mensagens/segundo) por uma duração
configurável, e repete para cada nível de carga definido em LOAD_LEVELS.
Ao final de cada nível, grava um CSV com todas as latências e um resumo
agregado (min/mean/median/p95/p99/max/jitter/perda).

Uso:
    python3 load_test.py

Os resultados vão para ./results/<timestamp>/load_<N>mps.csv e summary.csv
"""

import csv
import json
import os
import statistics
import threading
import time
import uuid
from datetime import datetime

import paho.mqtt.client as mqtt

# ===================== CONFIG =====================

MQTT_BROKER = "200.133.17.234"
MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 60

# Tópico de publicação (o broker precisa ecoar/republicar para medirmos RTT).
# Se você já tem um subscriber real (o bridge) consumindo TOPIC_PUB e
# publicando um ACK em TOPIC_ACK, ajuste os tópicos abaixo para bater com
# ele. Caso não haja um "echo" automático, use mosquitto's $SYS ou configure
# um pequeno echo script (ver echo_responder.py neste mesmo diretório).
TOPIC_PUB = "iomt/loadtest/dados"
TOPIC_ACK = "iomt/loadtest/ack"

QOS = 0  # mesmo QoS efetivo do pipeline (ver nota no firmware/bridge)

# Níveis de carga a testar, em mensagens por segundo (msg/s).
# Ajuste conforme a capacidade esperada do seu servidor (4 vCPU / 1.9GB RAM).
LOAD_LEVELS = [1, 5, 10, 20, 50]

# Duração de cada nível de carga, em segundos.
DURATION_PER_LEVEL_S = 60

# Timeout de espera por um ACK antes de considerar a mensagem perdida (s).
ACK_TIMEOUT_S = 5

RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "results",
    datetime.now().strftime("%Y%m%d_%H%M%S"),
)

# ====================================================

os.makedirs(RESULTS_DIR, exist_ok=True)

_pending = {}       # msg_id -> t_start
_pending_lock = threading.Lock()
_latencies = []      # lista de L_i (ms) na ordem de recebimento do ACK
_latencies_lock = threading.Lock()


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe(TOPIC_ACK, qos=QOS)
    else:
        print(f"[ERRO] Falha ao conectar ao broker, rc={rc}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        msg_id = data["id"]
        t_end = time.time()
        with _pending_lock:
            t_start = _pending.pop(msg_id, None)
        if t_start is not None:
            latency_ms = (t_end - t_start) * 1000.0
            with _latencies_lock:
                _latencies.append(latency_ms)
    except Exception as exc:
        print(f"[ERRO] on_message: {exc}")


def publish_loop(client, rate_mps: int, duration_s: int):
    interval = 1.0 / rate_mps
    end_time = time.time() + duration_s
    sent = 0
    while time.time() < end_time:
        msg_id = str(uuid.uuid4())
        t_start = time.time()
        with _pending_lock:
            _pending[msg_id] = t_start
        payload = json.dumps({"id": msg_id, "t_start": t_start})
        client.publish(TOPIC_PUB, payload, qos=QOS)
        sent += 1
        time.sleep(interval)
    return sent


def compute_jitter(latencies_ordered):
    """Jitter = diferença absoluta entre latências consecutivas (ms)."""
    if len(latencies_ordered) < 2:
        return []
    return [abs(latencies_ordered[i] - latencies_ordered[i - 1]) for i in range(1, len(latencies_ordered))]


def percentile(data, p):
    if not data:
        return float("nan")
    data_sorted = sorted(data)
    k = (len(data_sorted) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(data_sorted) - 1)
    if f == c:
        return data_sorted[f]
    return data_sorted[f] + (data_sorted[c] - data_sorted[f]) * (k - f)


def run_level(client, rate_mps: int):
    global _latencies
    with _latencies_lock:
        _latencies = []
    with _pending_lock:
        _pending.clear()

    print(f"\n=== Carga: {rate_mps} msg/s por {DURATION_PER_LEVEL_S}s ===")
    sent = publish_loop(client, rate_mps, DURATION_PER_LEVEL_S)

    # Espera final para dar tempo dos últimos ACKs chegarem
    time.sleep(ACK_TIMEOUT_S)

    with _latencies_lock:
        received_latencies = list(_latencies)
    with _pending_lock:
        lost = len(_pending)

    received = len(received_latencies)
    loss_pct = (lost / sent * 100.0) if sent else 0.0
    jitter_values = compute_jitter(received_latencies)

    # Grava CSV bruto com todas as latências desse nível
    csv_path = os.path.join(RESULTS_DIR, f"load_{rate_mps}mps.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["latency_ms"])
        for lat in received_latencies:
            writer.writerow([lat])

    summary = {
        "load_mps": rate_mps,
        "duration_s": DURATION_PER_LEVEL_S,
        "sent": sent,
        "received": received,
        "lost": lost,
        "loss_pct": round(loss_pct, 3),
        "latency_min_ms": round(min(received_latencies), 3) if received_latencies else None,
        "latency_mean_ms": round(statistics.mean(received_latencies), 3) if received_latencies else None,
        "latency_median_ms": round(statistics.median(received_latencies), 3) if received_latencies else None,
        "latency_p95_ms": round(percentile(received_latencies, 95), 3) if received_latencies else None,
        "latency_p99_ms": round(percentile(received_latencies, 99), 3) if received_latencies else None,
        "latency_max_ms": round(max(received_latencies), 3) if received_latencies else None,
        "latency_stdev_ms": round(statistics.stdev(received_latencies), 3) if len(received_latencies) > 1 else None,
        "jitter_mean_ms": round(statistics.mean(jitter_values), 3) if jitter_values else None,
        "jitter_stdev_ms": round(statistics.stdev(jitter_values), 3) if len(jitter_values) > 1 else None,
    }

    print(f"Enviadas: {sent} | Recebidas: {received} | Perdidas: {lost} ({loss_pct:.2f}%)")
    if received_latencies:
        print(
            f"Latência: min={summary['latency_min_ms']}ms  "
            f"mean={summary['latency_mean_ms']}ms  "
            f"p95={summary['latency_p95_ms']}ms  "
            f"p99={summary['latency_p99_ms']}ms  "
            f"max={summary['latency_max_ms']}ms"
        )
        print(f"Jitter: mean={summary['jitter_mean_ms']}ms  stdev={summary['jitter_stdev_ms']}ms")

    return summary


def main():
    client = mqtt.Client(
        client_id=f"loadtest-{uuid.uuid4().hex[:8]}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=MQTT_KEEPALIVE_S)
    client.loop_start()

    time.sleep(1)  # aguarda subscribe confirmar

    all_summaries = []
    for rate in LOAD_LEVELS:
        summary = run_level(client, rate)
        all_summaries.append(summary)
        time.sleep(5)  # intervalo de resfriamento entre níveis de carga

    client.loop_stop()
    client.disconnect()

    summary_path = os.path.join(RESULTS_DIR, "summary.csv")
    with open(summary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_summaries[0].keys()))
        writer.writeheader()
        for row in all_summaries:
            writer.writerow(row)

    print(f"\nResultados salvos em: {RESULTS_DIR}")
    print(f"Resumo consolidado: {summary_path}")


if __name__ == "__main__":
    main()
