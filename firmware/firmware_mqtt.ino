#include <ESP8266WiFi.h>
#include <PubSubClient.h>

// ===================== Parâmetros de rede =====================
const char* rede = "ML";
const char* senha = "12345678";

// ===================== Parâmetros do Broker MQTT =====================
const char* mqtt_server   = "200.133.17.234";
const int   mqtt_port     = 1883;
const char* mqtt_client_id = "esp8266-paciente-01";
const char* mqtt_topic    = "iomt/paciente/dados";

// QoS: 0 = at most once, 1 = at least once, 2 = exactly once.
// ATENÇÃO: a biblioteca PubSubClient (usada abaixo) só implementa publish()
// com QoS 0 -- não há suporte real a QoS 1/2 nesta lib, mesmo que se declare
// a constante abaixo. Se for necessário QoS 1/2 de fato, use a lib
// "AsyncMqttClient" ou "espMqttClient", que suportam ACK/PUBREC corretamente.
// Portanto, o valor efetivo e documentável para este firmware é QoS 0.
const uint8_t MQTT_QOS = 0;

// Keep-alive: intervalo (s) entre PINGs de manutenção da sessão com o broker.
// PubSubClient permite configurar via setKeepAlive() a partir da versão 2.8.
const uint16_t MQTT_KEEPALIVE_S = 60;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ===================== Estrutura de dados (idêntica à original) =====================
struct Amostra {
  uint8_t id;   // ID do Paciente
  uint8_t bpm;  // Batimentos por Minuto
  uint8_t spo2; // Saturação de Oxigênio
};

// --- TODOS OS DADOS ARMAZENADOS NA MEMÓRIA FLASH (PROGMEM) ---
const Amostra dados[] PROGMEM = {
  // --- Paciente 1 (ID: 36) ---
  {36, 85, 98}, {36, 88, 97}, {36, 90, 98}, {36, 92, 98}, {36, 88, 96}, {36, 95, 97}, {36, 100, 97}, {36, 96, 95}, {36, 94, 98}, {36, 98, 98},
  {36, 97, 99}, {36, 95, 98}, {36, 93, 98}, {36, 90, 99}, {36, 88, 97}, {36, 86, 98}, {36, 89, 97}, {36, 91, 96}, {36, 94, 95}, {36, 96, 97},
  {36, 99, 98}, {36, 101, 97}, {36, 98, 98}, {36, 95, 99}, {36, 92, 98}, {36, 90, 99}, {36, 88, 98}, {36, 87, 97}, {36, 89, 96}, {36, 92, 95},
  {36, 95, 96}, {36, 98, 97}, {36, 100, 98}, {36, 97, 99}, {36, 94, 98}, {36, 91, 97}, {36, 89, 98}, {36, 88, 99}, {36, 90, 98}, {36, 93, 97},
  {36, 96, 96}, {36, 99, 95}, {36, 102, 96}, {36, 100, 97}, {36, 98, 98}, {36, 96, 99}, {36, 94, 98}, {36, 92, 97}, {36, 90, 98}, {36, 89, 98},
  // (... restante do array de amostras permanece IDÊNTICO ao firmware original;
  //  omitido aqui por brevidade -- copie o array completo do arquivo original) ...
};

int totalDados = sizeof(dados) / sizeof(dados[0]);
int indice = 0;

void conectarWiFi() {
  Serial.print("Conectando à rede: ");
  Serial.println(rede);
  WiFi.mode(WIFI_STA);
  WiFi.begin(rede, senha);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConectado! IP: ");
  Serial.println(WiFi.localIP());
}

void reconectarMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Conectando ao broker MQTT...");
    if (mqttClient.connect(mqtt_client_id)) {
      Serial.println(" conectado!");
    } else {
      Serial.print(" falhou, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" tentando novamente em 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  conectarWiFi();

  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setKeepAlive(MQTT_KEEPALIVE_S);
}

void loop() {
  if (!mqttClient.connected()) {
    reconectarMQTT();
  }
  mqttClient.loop();

  if (WiFi.status() == WL_CONNECTED && indice < totalDados) {
    publicarAmostra(indice);
    indice++;
  } else if (indice >= totalDados) {
    if (indice == totalDados) {
      Serial.println("✅ Todos os dados foram publicados.");
      indice++;
    }
  }
  delay(500); // Mesmo intervalo de envio do firmware original
}

void publicarAmostra(int idx) {
  Amostra amostra_atual;
  memcpy_P(&amostra_atual, &dados[idx], sizeof(Amostra));

  // Payload em InfluxDB line protocol -- o bridge MQTT->InfluxDB no servidor
  // repassa esse payload diretamente para a API de escrita do InfluxDB.
  char payload[100];
  sprintf(payload, "paciente,id=%u bpm=%u,spo2=%u",
          amostra_atual.id,
          amostra_atual.bpm,
          amostra_atual.spo2);

  bool ok = mqttClient.publish(mqtt_topic, payload, false); // retained=false

  Serial.print("➡️ Publicando ");
  Serial.print(idx + 1);
  Serial.print("/");
  Serial.print(totalDados);
  Serial.print(" no tópico '");
  Serial.print(mqtt_topic);
  Serial.print("': ");
  Serial.print(payload);
  Serial.println(ok ? "  [OK]" : "  [FALHA]");
}
