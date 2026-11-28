import 'package:flutter/material.dart';

import '../config/app_colors.dart';
import '../config/localization.dart';
import 'sensor_data.dart';

/// The five readings of the ESP32 monitor, with everything a screen needs to
/// show one of them: label, unit, colours, safe zone and status.
enum SensorMetric {
  airTemperature,
  airHumidity,
  airQuality,
  soilTemperature,
  soilMoisture;

  String get label => switch (this) {
        airTemperature => S.current.airTemperature,
        airHumidity => S.current.airHumidity,
        airQuality => S.current.airQuality,
        soilTemperature => S.current.soilTemperature,
        soilMoisture => S.current.soilMoisture,
      };

  /// English name for the AI prompts.
  String get englishName => switch (this) {
        airTemperature => 'Air temperature',
        airHumidity => 'Air humidity',
        airQuality => 'Air quality',
        soilTemperature => 'Soil temperature',
        soilMoisture => 'Soil moisture',
      };

  String get unit => switch (this) {
        airTemperature || soilTemperature => '°C',
        airHumidity || soilMoisture => '%',
        airQuality => 'PPM',
      };

  IconData get icon => switch (this) {
        airTemperature => Icons.thermostat_outlined,
        airHumidity => Icons.water_drop_outlined,
        airQuality => Icons.air_rounded,
        soilTemperature => Icons.device_thermostat_rounded,
        soilMoisture => Icons.grass_rounded,
      };

  Color get color => switch (this) {
        airTemperature => AppColors.cyan,
        airHumidity => AppColors.mint,
        airQuality => AppColors.amber,
        soilTemperature => AppColors.soil,
        soilMoisture => AppColors.leaf,
      };

  bool get isSoil => this == soilTemperature || this == soilMoisture;

  /// Digits after the decimal point on the dashboard card.
  int get decimals => this == airQuality ? 0 : 1;

  /// The reading, or null when this row does not have it.
  double? valueOf(SensorData d) => switch (this) {
        airTemperature => d.temperature,
        airHumidity => d.humidity,
        airQuality => d.airQualityPpm,
        soilTemperature => d.soilTemperature,
        soilMoisture => d.soilMoisture,
      };

  /// NORMAL / WARNING / CRITICAL, or null when the row does not have the reading.
  String? statusOf(SensorData d) => switch (this) {
        airTemperature => d.temperatureStatus,
        airHumidity => d.humidityStatus,
        airQuality => d.airPpmStatus,
        soilTemperature => d.soilTemperatureStatus,
        soilMoisture => d.soilMoistureStatus,
      };

  /// Recommended range, drawn as dashed lines on the detail chart.
  double get safeMin => switch (this) {
        airTemperature => 18,
        airHumidity => 40,
        airQuality => 0,
        soilTemperature => 10,
        soilMoisture => 20,
      };

  double get safeMax => switch (this) {
        airTemperature => 22,
        airHumidity => 50,
        airQuality => 1000,
        soilTemperature => 25,
        soilMoisture => 60,
      };

  /// Physical minimum of the chart axis (0 %, 0 ppm); temperatures have none.
  double? get axisFloor => switch (this) {
        airTemperature || soilTemperature => null,
        _ => 0,
      };
}
