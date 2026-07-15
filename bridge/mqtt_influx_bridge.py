#!/usr/bin/env python3
"""
Bridge MQTT -> InfluxDB (v1.x) para a arquitetura IoMT.

Subscreve o tópico publicado pelo firmware do ESP8266 e grava os pontos no
InfluxDB 1.6.7, com batching manual (batch_size / flush_interval), já que o
cliente clássico do InfluxDB 1.x não tem flush automático por tempo como o
cliente 2.x.

Todos os parâmetros relevantes para o artigo (QoS, keep-alive, batch size,
flush interval) ficam centralizados no bloco CONFIG abaixo -- são os valores
reais e efetivamente usados neste pipeline, não estimativas.
"""

import logging
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt
from influxdb import InfluxDBClient

# ===================== CONFIG =====================

# --- MQTT ---
MQTT_BROKER = "200.133.17.234"
MQTT_PORT = 1883
MQTT_TOPIC = "iomt/paciente/dados"
MQTT_CLIENT_ID = "bridge-influx-01"

# QoS na subscrição do bridge. O broker (Mosquitto) suporta QoS 0/1/2, mas o
# publisher (ESP8266 + PubSubClient) só publica em QoS 0 -- ver nota no
# firmware. Portanto, o QoS efetivo do pipeline ponta-a-ponta é 0, mesmo que
# o bridge subscreva com QoS 1.
MQTT_SUBSCRIBE_QOS = 1

# Keep-alive (s): intervalo entre PINGREQ/PINGRESP para manter a sessão viva.
MQTT_KEEPALIVE_S = 60

# --- InfluxDB (v1.6.7) ---
INFLUX_HOST = "200.133.17.234"
INFLUX_PORT = 8086
INFLUX_DATABASE = "mqtt_data"      # já existe, é o mesmo banco usado hoje
INFLUX_USERNAME = None             # preencher se o servidor exigir auth
INFLUX_PASSWORD = None             # preencher se o servidor exigir auth
INFLUX_MEASUREMENT = "paciente"

# Batching: nº de pontos acumulados antes de um write, OU o flush_interval
# (o que ocorrer primeiro). Esses dois valores são os que devem ser reportados
# na tabela de parâmetros do artigo. O cliente v1 não tem flush por tempo
# nativo, então implementamos com um timer em thread separada (ver Flusher).
INFLUX_BATCH_SIZE = 50            # pontos por lote
INFLUX_FLUSH_INTERVAL_MS = 2000   # ms -- força o flush mesmo sem lote completo

# ====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("mqtt_influx_bridge")

influx_client = InfluxDBClient(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    username=INFLUX_USERNAME,
    password=INFLUX_PASSWORD,
    database=INFLUX_DATABASE,
)

_buffer = []
_buffer_lock = threading.Lock()
_stop_event = threading.Event()


def parse_line_protocol_payload(payload: str) -> dict:
    """
    O firmware publica o payload já em InfluxDB line protocol, ex:
    'paciente,id=36 bpm=90,spo2=98'
    Convertemos para o dict que o cliente influxdb v1 espera em write_points.
    """
    tags_fields = payload.split(" ")
    measurement_and_tags = tags_fields[0].split(",")
    measurement = measurement_and_tags[0]
    tags = dict(t.split("=") for t in measurement_and_tags[1:])
    fields = {k: int(v) for k, v in (f.split("=") for f in tags_fields[1].split(","))}

    return {
        "measurement": measurement,
        "tags": tags,
        "fields": fields,
        "time": int(time.time() * 1e9),  # nanosegundos
    }


def flush_buffer(reason: str = "batch_size"):
    with _buffer_lock:
        if not _buffer:
            return
        points = _buffer[:]
        _buffer.clear()
    try:
        influx_client.write_points(points, time_precision="n")
        log.info("Flush (%s): %d pontos gravados no InfluxDB", reason, len(points))
    except Exception as exc:
        log.exception("Erro ao gravar lote no InfluxDB: %s", exc)


def flusher_loop():
    """Thread separada: força flush a cada INFLUX_FLUSH_INTERVAL_MS,
    mesmo que o lote não tenha atingido INFLUX_BATCH_SIZE."""
    interval_s = INFLUX_FLUSH_INTERVAL_MS / 1000.0
    while not _stop_event.wait(interval_s):
        flush_buffer(reason="flush_interval")


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Conectado ao broker MQTT %s:%s", MQTT_BROKER, MQTT_PORT)
        client.subscribe(MQTT_TOPIC, qos=MQTT_SUBSCRIBE_QOS)
        log.info("Inscrito no tópico '%s' com QoS=%s", MQTT_TOPIC, MQTT_SUBSCRIBE_QOS)
    else:
        log.error("Falha ao conectar ao broker, rc=%s", rc)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        point = parse_line_protocol_payload(payload)
        with _buffer_lock:
            _buffer.append(point)
            should_flush = len(_buffer) >= INFLUX_BATCH_SIZE
        if should_flush:
            flush_buffer(reason="batch_size")
        log.debug("Ponto enfileirado: %s", payload)
    except Exception as exc:
        log.exception("Erro ao processar mensagem '%s': %s", msg.payload, exc)


def on_disconnect(client, userdata, rc, properties=None):
    log.warning("Desconectado do broker (rc=%s). Tentando reconectar...", rc)


def shutdown(signum, frame):
    log.info("Encerrando bridge...")
    _stop_event.set()
    flush_buffer(reason="shutdown")
    mqtt_client.disconnect()
    sys.exit(0)


if __name__ == "__main__":
    mqtt_client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    flusher_thread = threading.Thread(target=flusher_loop, daemon=True)
    flusher_thread.start()

    log.info("Iniciando bridge MQTT -> InfluxDB (v1.6.7)")
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=MQTT_KEEPALIVE_S)
    mqtt_client.loop_forever()
