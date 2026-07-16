#!/usr/bin/env python3
"""
Echo responder para o teste de carga.

Assina TOPIC_PUB e, para cada mensagem recebida, republica o mesmo `id` em
TOPIC_ACK. Isso simula o comportamento de confirmação (ACK) que o teste de
latência original (Seção 6.4 do artigo) mede como RTT.

Rode este script em paralelo com load_test.py, ou deixe-o rodando como
serviço enquanto os testes de carga são executados. Ele não grava nada no
InfluxDB -- é só o "eco" para fins de medição de latência ponta-a-ponta via
o broker MQTT.

Uso:
    python3 echo_responder.py
"""

import json
import time

import paho.mqtt.client as mqtt

MQTT_BROKER = "200.133.17.234"
MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 60

TOPIC_PUB = "iomt/loadtest/dados"
TOPIC_ACK = "iomt/loadtest/ack"
QOS = 0


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[echo] Conectado ao broker, assinando '{TOPIC_PUB}'")
        client.subscribe(TOPIC_PUB, qos=QOS)
    else:
        print(f"[echo] Falha ao conectar, rc={rc}")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        ack_payload = json.dumps({"id": data["id"]})
        client.publish(TOPIC_ACK, ack_payload, qos=QOS)
    except Exception as exc:
        print(f"[echo] Erro ao processar mensagem: {exc}")


if __name__ == "__main__":
    client = mqtt.Client(
        client_id="loadtest-echo-responder",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=MQTT_KEEPALIVE_S)
    print("[echo] Rodando. Ctrl+C para parar.")
    client.loop_forever()
