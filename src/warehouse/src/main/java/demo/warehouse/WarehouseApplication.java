package demo.warehouse;

import java.math.BigDecimal;
import java.net.http.HttpClient;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;

/**
 * Spring Boot 3 / Java 21 service for the OBI showcase. It exercises the
 * patterns an enterprise Spring estate uses: Spring MVC on virtual threads,
 * JDBC through HikariCP, an outbound HTTPS call made from a platform thread
 * pool, and spring-kafka produce + consume. It contains no telemetry code.
 */
@SpringBootApplication
public class WarehouseApplication {

  public static void main(String[] args) {
    SpringApplication.run(WarehouseApplication.class, args);
  }

  @Bean
  NewTopic warehouseEvents(@Value("${warehouse.topic}") String topic) {
    return TopicBuilder.name(topic).partitions(1).replicas(1).build();
  }

  /** Bounded platform-thread pool: the HTTPS call to fx hops onto it. */
  @Bean(destroyMethod = "shutdown")
  ExecutorService fxPool() {
    return Executors.newFixedThreadPool(4);
  }

  /**
   * RestClient over the JDK HttpClient (java.net.http, TLS via SSLEngine).
   * The fx services use a self-signed certificate, so this demo-only client
   * trusts any certificate. Never do this outside a demo.
   */
  @Bean
  RestClient fxClient() throws Exception {
    TrustManager[] trustAll = {new X509TrustManager() {
      public void checkClientTrusted(X509Certificate[] c, String a) {}
      public void checkServerTrusted(X509Certificate[] c, String a) {}
      public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    }};
    SSLContext ssl = SSLContext.getInstance("TLS");
    ssl.init(null, trustAll, new SecureRandom());
    HttpClient http = HttpClient.newBuilder().sslContext(ssl)
        .version(HttpClient.Version.HTTP_1_1).build();
    return RestClient.builder().requestFactory(new JdkClientHttpRequestFactory(http)).build();
  }

  @RestController
  static class StockController {
    private final JdbcTemplate jdbc;
    private final KafkaTemplate<String, String> kafka;
    private final RestClient fx;
    private final ExecutorService pool;
    private final String topic, fxFull, fxSlim;

    StockController(JdbcTemplate jdbc, KafkaTemplate<String, String> kafka, RestClient fxClient,
        ExecutorService fxPool, @Value("${warehouse.topic}") String topic,
        @Value("${warehouse.fx.full-url}") String fxFull,
        @Value("${warehouse.fx.slim-url}") String fxSlim) {
      this.jdbc = jdbc; this.kafka = kafka; this.fx = fxClient; this.pool = fxPool;
      this.topic = topic; this.fxFull = fxFull; this.fxSlim = fxSlim;
    }

    @GetMapping("/api/stock/{sku}")
    Map<String, Object> stock(@PathVariable String sku) {
      return jdbc.queryForList(
              "SELECT sku, quantity, price_usd FROM warehouse.stock WHERE sku = ?", sku)
          .stream().findFirst()
          .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, sku));
    }

    /** DB update, HTTPS call on a pool thread, Kafka publish. */
    @PostMapping("/api/stock/{sku}/reserve")
    Map<String, Object> reserve(@PathVariable String sku,
        @RequestParam(defaultValue = "EUR") String ccy,
        @RequestParam(defaultValue = "full") String fxVariant) {
      // Restocks when empty so the demo's continuous traffic never runs dry.
      int updated = jdbc.update(
          "UPDATE warehouse.stock SET quantity = CASE WHEN quantity > 0 THEN quantity - 1"
              + " ELSE 100000 END WHERE sku = ?", sku);
      if (updated == 0) throw new ResponseStatusException(HttpStatus.NOT_FOUND, sku);
      BigDecimal usd = jdbc.queryForObject(
          "SELECT price_usd FROM warehouse.stock WHERE sku = ?", BigDecimal.class, sku);
      String base = "slim".equals(fxVariant) ? fxSlim : fxFull;
      Map<?, ?> converted = CompletableFuture.supplyAsync(() -> fx.get()
              .uri(base + "/convert?amount={a}&ccy={c}", usd, ccy)
              .retrieve().body(Map.class), pool)
          .join();
      kafka.send(topic, sku, "reserved:" + sku);
      return Map.of("sku", sku, "usd", usd, "fx", converted, "fxVariant", fxVariant);
    }

    /** Called back by the fx services (Python) for exchange rates. */
    @GetMapping("/api/rates/{ccy}")
    Map<String, Object> rate(@PathVariable String ccy) {
      double r = switch (ccy) { case "EUR" -> 0.92; case "GBP" -> 0.79; case "JPY" -> 149.3; default -> 1.0; };
      return Map.of("ccy", ccy, "rate", r);
    }

    @KafkaListener(topics = "${warehouse.topic}")
    void onEvent(String value) {
      String sku = value.substring(value.indexOf(':') + 1);
      jdbc.update("INSERT INTO warehouse.audit (sku, event) VALUES (?, ?)", sku, value);
    }
  }
}
