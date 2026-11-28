/// One row of `public.sensor_data` (ESP32 monitor, monitoring.cpp).
///
/// [temperature] and [humidity] are the AIR temperature and humidity (DHT11),
/// [airQuality] is the raw MQ-135 ADC value (0..4095). The soil values and
/// [airPpm] were added later (migration 20261128000002) and are null in older
/// rows, from an older firmware or when that sensor is not connected.
class SensorData {
  final int? id;
  final double temperature;
  final double humidity;
  final double airQuality;
  final double? airPpm;
  final double? soilMoisture;
  final double? soilTemperature;
  final DateTime createdAt;

  SensorData({
    this.id,
    required this.temperature,
    required this.humidity,
    required this.airQuality,
    this.airPpm,
    this.soilMoisture,
    this.soilTemperature,
    required this.createdAt,
  });

  static double? _optional(Object? value) {
    if (value is! num) return null;
    final v = value.toDouble();
    return v.isFinite ? v : null;
  }

  factory SensorData.fromJson(Map<String, dynamic> json) {
    return SensorData(
      id: json['id'] as int?,
      temperature: (json['temperature'] as num?)?.toDouble() ?? 0.0,
      humidity: (json['humidity'] as num?)?.toDouble() ?? 0.0,
      airQuality: (json['air_quality'] as num?)?.toDouble() ?? 0.0,
      airPpm: _optional(json['air_ppm']),
      soilMoisture: _optional(json['soil_moisture']),
      soilTemperature: _optional(json['soil_temperature']),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'temperature': temperature,
      'humidity': humidity,
      'air_quality': airQuality,
      if (airPpm != null) 'air_ppm': airPpm,
      if (soilMoisture != null) 'soil_moisture': soilMoisture,
      if (soilTemperature != null) 'soil_temperature': soilTemperature,
      'created_at': createdAt.toIso8601String(),
    };
  }

  /// Air quality in ppm. Rows from before the ppm column only have the raw
  /// sensor value, which is shown instead.
  double get airQualityPpm => airPpm ?? airQuality;

  String get temperatureStatus {
    if (temperature < 15 || temperature > 30) return 'CRITICAL';
    if (temperature < 18 || temperature > 26) return 'WARNING';
    return 'NORMAL';
  }

  String get humidityStatus {
    if (humidity < 30 || humidity > 70) return 'CRITICAL';
    if (humidity < 40 || humidity > 60) return 'WARNING';
    return 'NORMAL';
  }

  /// Status of the raw MQ-135 value (rows without [airPpm]).
  String get airQualityStatus {
    if (airQuality > 1000) return 'CRITICAL';
    if (airQuality > 500) return 'WARNING';
    return 'NORMAL';
  }

  /// Air quality status: ppm thresholds (CO2 equivalent) when the row has
  /// [airPpm], otherwise the raw value status.
  String get airPpmStatus {
    final ppm = airPpm;
    if (ppm == null) return airQualityStatus;
    if (ppm > 2000) return 'CRITICAL';
    if (ppm > 1000) return 'WARNING';
    return 'NORMAL';
  }

  /// Null when the row has no soil moisture.
  String? get soilMoistureStatus {
    final m = soilMoisture;
    if (m == null) return null;
    if (m < 10 || m > 80) return 'CRITICAL';
    if (m < 20 || m > 60) return 'WARNING';
    return 'NORMAL';
  }

  /// Null when the row has no soil temperature.
  String? get soilTemperatureStatus {
    final t = soilTemperature;
    if (t == null) return null;
    if (t < 0 || t > 35) return 'CRITICAL';
    if (t < 10 || t > 25) return 'WARNING';
    return 'NORMAL';
  }
}
