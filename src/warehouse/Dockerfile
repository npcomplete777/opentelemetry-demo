# Spring Boot 3 / Java 21 warehouse service. No OpenTelemetry agent, SDK or
# JAVA_TOOL_OPTIONS: OBI is the only source of telemetry for it.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B -DskipTests package && cp target/warehouse-*.jar /app.jar

FROM eclipse-temurin:21-jre
COPY --from=build /app.jar /app/warehouse.jar
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/warehouse.jar"]
