class SensorData {
  final int? id;
  final double temperature;
  final double humidity;
  final double airQuality;
  final DateTime createdAt;

  SensorData({
    this.id,
    required this.temperature,
    required this.humidity,
    required this.airQuality,
    required this.createdAt,
  });

  factory SensorData.fromJson(Map<String, dynamic> json) {
    return SensorData(
      id: json['id'] as int?,
      temperature: (json['temperature'] as num?)?.toDouble() ?? 0.0,
      humidity: (json['humidity'] as num?)?.toDouble() ?? 0.0,
      airQuality: (json['air_quality'] as num?)?.toDouble() ?? 0.0,
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'temperature': temperature,
      'humidity': humidity,
      'air_quality': airQuality,
      'created_at': createdAt.toIso8601String(),
    };
  }

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

  String get airQualityStatus {
    if (airQuality > 1000) return 'CRITICAL';
    if (airQuality > 500) return 'WARNING';
    return 'NORMAL';
  }
}
