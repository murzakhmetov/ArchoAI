import 'dart:math' as math;

import 'package:archoai_app/models/sensor_data.dart';
import 'package:archoai_app/screens/dashboard/dashboard_screen.dart';
import 'package:archoai_app/screens/dashboard/metric_detail_screen.dart';
import 'package:flutter_test/flutter_test.dart';

SensorData _reading({double t = 22, double h = 45, double a = 400}) => SensorData(
      temperature: t,
      humidity: h,
      airQuality: a,
      createdAt: DateTime.utc(2026, 1, 1),
    );

void main() {
  group('SensorData.fromJson', () {
    test('parses a full row and accepts ints for the numeric fields', () {
      final data = SensorData.fromJson({
        'id': 7,
        'temperature': 21.5,
        'humidity': 48,
        'air_quality': 412,
        'created_at': '2026-03-01T10:20:30+00:00',
      });

      expect(data.id, 7);
      expect(data.temperature, 21.5);
      expect(data.humidity, 48.0);
      expect(data.humidity, isA<double>());
      expect(data.airQuality, 412.0);
      expect(data.createdAt.toUtc(), DateTime.utc(2026, 3, 1, 10, 20, 30));
    });

    test('falls back to zeros and a null id for missing or null fields', () {
      final data = SensorData.fromJson({'temperature': null});

      expect(data.id, isNull);
      expect(data.temperature, 0.0);
      expect(data.humidity, 0.0);
      expect(data.airQuality, 0.0);
    });

    test('uses the current time when created_at is missing or unparsable', () {
      for (final json in <Map<String, dynamic>>[
        {},
        {'created_at': 'yesterday-ish'},
      ]) {
        final data = SensorData.fromJson(json);
        expect(DateTime.now().difference(data.createdAt).inSeconds.abs(), lessThan(5));
      }
    });
  });

  group('SensorData.toJson', () {
    test('uses the database column names and leaves the id out', () {
      final json = SensorData(
        id: 3,
        temperature: 20.5,
        humidity: 44,
        airQuality: 380,
        createdAt: DateTime.utc(2026, 2, 3, 4, 5, 6),
      ).toJson();

      expect(json, {
        'temperature': 20.5,
        'humidity': 44.0,
        'air_quality': 380.0,
        'created_at': '2026-02-03T04:05:06.000Z',
      });
    });

    test('survives a round trip through fromJson', () {
      final original = _reading(t: 19.25, h: 51.5, a: 733);
      final copy = SensorData.fromJson(original.toJson());

      expect(copy.temperature, original.temperature);
      expect(copy.humidity, original.humidity);
      expect(copy.airQuality, original.airQuality);
      expect(copy.createdAt, original.createdAt);
    });
  });

  group('status getters', () {
    const temperature = <(double, String)>[
      (14.9, 'CRITICAL'),
      (15.0, 'WARNING'),
      (17.9, 'WARNING'),
      (18.0, 'NORMAL'),
      (26.0, 'NORMAL'),
      (26.1, 'WARNING'),
      (30.0, 'WARNING'),
      (30.1, 'CRITICAL'),
    ];
    const humidity = <(double, String)>[
      (29.9, 'CRITICAL'),
      (30.0, 'WARNING'),
      (39.9, 'WARNING'),
      (40.0, 'NORMAL'),
      (60.0, 'NORMAL'),
      (60.1, 'WARNING'),
      (70.0, 'WARNING'),
      (70.1, 'CRITICAL'),
    ];
    const airQuality = <(double, String)>[
      (0.0, 'NORMAL'),
      (500.0, 'NORMAL'),
      (500.1, 'WARNING'),
      (1000.0, 'WARNING'),
      (1000.1, 'CRITICAL'),
    ];

    for (final (value, expected) in temperature) {
      test('temperature $value is $expected', () {
        expect(_reading(t: value).temperatureStatus, expected);
      });
    }
    for (final (value, expected) in humidity) {
      test('humidity $value is $expected', () {
        expect(_reading(h: value).humidityStatus, expected);
      });
    }
    for (final (value, expected) in airQuality) {
      test('air quality $value is $expected', () {
        expect(_reading(a: value).airQualityStatus, expected);
      });
    }
  });

  group('isDeviceOnline', () {
    final now = DateTime(2026, 5, 1, 12, 0, 0);

    test('no reading at all means offline', () {
      expect(isDeviceOnline(null, now: now), isFalse);
    });

    test('a fresh reading is online', () {
      expect(isDeviceOnline(now, now: now), isTrue);
      expect(isDeviceOnline(now.subtract(const Duration(seconds: 10)), now: now), isTrue);
    });

    test('one second under two minutes is still online', () {
      expect(isDeviceOnline(now.subtract(const Duration(minutes: 1, seconds: 59)), now: now), isTrue);
    });

    test('exactly two minutes is already offline', () {
      expect(isDeviceOnline(now.subtract(const Duration(minutes: 2)), now: now), isFalse);
    });

    test('older readings are offline', () {
      expect(isDeviceOnline(now.subtract(const Duration(minutes: 5)), now: now), isFalse);
      expect(isDeviceOnline(now.subtract(const Duration(hours: 3)), now: now), isFalse);
    });

    test('a timestamp slightly ahead of the clock (clock skew) counts as online', () {
      expect(isDeviceOnline(now.add(const Duration(seconds: 30)), now: now), isTrue);
    });

    test('a timestamp far in the future is a data error, not a live device', () {
      expect(isDeviceOnline(now.add(const Duration(days: 365)), now: now), isFalse);
      expect(isDeviceOnline(now.add(const Duration(minutes: 6)), now: now), isFalse);
      expect(isDeviceOnline(now.add(const Duration(minutes: 4)), now: now), isTrue);
    });

    test('compares instants, so UTC and local timestamps mix safely', () {
      final recentUtc = now.toUtc().subtract(const Duration(minutes: 1));
      final staleUtc = now.toUtc().subtract(const Duration(minutes: 3));
      expect(isDeviceOnline(recentUtc, now: now), isTrue);
      expect(isDeviceOnline(staleUtc, now: now), isFalse);
    });

    test('uses the system clock when no now is given', () {
      expect(isDeviceOnline(DateTime.now().subtract(const Duration(seconds: 1))), isTrue);
      expect(isDeviceOnline(DateTime.now().subtract(const Duration(minutes: 10))), isFalse);
    });
  });

  group('computeChartRange', () {
    void expectRange(ChartRange range, double minY, double maxY, double interval) {
      expect(range.minY, closeTo(minY, 1e-9), reason: 'minY');
      expect(range.maxY, closeTo(maxY, 1e-9), reason: 'maxY');
      expect(range.interval, closeTo(interval, 1e-9), reason: 'interval');
    }

    test('air quality in the generated 125-175 range is no longer cut off', () {
      // The old fixed axis started at 300 and hid all of this data.
      final range = computeChartRange(
        [for (var v = 125.0; v <= 175; v += 5) v],
        safeMin: 0,
        safeMax: 500,
        floor: 0,
      );
      expectRange(range, 0, 600, 100);
    });

    test('air quality above the old fixed 600 ceiling still fits', () {
      final range = computeChartRange([400, 640, 880], safeMin: 0, safeMax: 500, floor: 0);
      expectRange(range, 0, 1000, 200);
    });

    test('raw ADC values up to 4095 get a coarse grid, not 80 labels', () {
      final range = computeChartRange([180, 2048, 4095], safeMin: 0, safeMax: 500, floor: 0);
      expectRange(range, 0, 5000, 1000);
    });

    test('temperature range hugs the data and the safe zone', () {
      final range = computeChartRange([20, 22, 24], safeMin: 18, safeMax: 22);
      expectRange(range, 16, 26, 2);
    });

    test('humidity range hugs the data and the safe zone', () {
      final range = computeChartRange([45, 50, 54], safeMin: 40, safeMax: 50, floor: 0);
      expectRange(range, 35, 60, 5);
    });

    test('a spike far outside the safe zone widens the range', () {
      final range = computeChartRange([21, 45], safeMin: 18, safeMax: 22);
      expectRange(range, 10, 50, 10);
    });

    test('no data falls back to the safe zone with padding', () {
      final range = computeChartRange(const <double>[], safeMin: 18, safeMax: 22);
      expectRange(range, 17, 23, 1);
    });

    test('a constant value inside the safe zone gives the same range as no data', () {
      final constant = computeChartRange([20, 20, 20], safeMin: 18, safeMax: 22);
      final empty = computeChartRange(const <double>[], safeMin: 18, safeMax: 22);
      expectRange(constant, empty.minY, empty.maxY, empty.interval);
    });

    test('a collapsed range (no spread at all) still gets room to draw', () {
      final range = computeChartRange([5, 5], safeMin: 5, safeMax: 5);
      expectRange(range, 4, 6, 0.5);
    });

    test('the floor keeps the axis from going below zero', () {
      final range = computeChartRange([0.5, 3], safeMin: 40, safeMax: 50, floor: 0);
      expectRange(range, 0, 60, 10);
    });

    test('without a floor, negative temperatures extend the axis down', () {
      final range = computeChartRange([-5, 0], safeMin: 18, safeMax: 22);
      expectRange(range, -10, 30, 10);
    });

    test('NaN and infinite values are ignored', () {
      final clean = computeChartRange([21], safeMin: 18, safeMax: 22);
      final dirty = computeChartRange([double.nan, 21, double.infinity, double.negativeInfinity], safeMin: 18, safeMax: 22);
      expectRange(dirty, clean.minY, clean.maxY, clean.interval);
    });

    test('absurdly large readings neither crash the maths nor stretch the axis', () {
      final clean = computeChartRange(const <double>[], safeMin: 0, safeMax: 500, floor: 0);
      final garbage = computeChartRange([1.7e308, -1.7e308, 1e13], safeMin: 0, safeMax: 500, floor: 0);
      expectRange(garbage, clean.minY, clean.maxY, clean.interval);
    });

    test('swapped safe bounds are treated as a range', () {
      final range = computeChartRange(const <double>[], safeMin: 22, safeMax: 18);
      expectRange(range, 17, 23, 1);
    });

    test('invariants hold for a wide spread of random inputs', () {
      final random = math.Random(42);
      for (var i = 0; i < 500; i++) {
        final scale = math.pow(10, random.nextInt(5) - 1).toDouble(); // 0.1 .. 1000
        final safeMin = random.nextDouble() * scale;
        final safeMax = safeMin + random.nextDouble() * scale;
        final count = 1 + random.nextInt(30);
        final values = [for (var n = 0; n < count; n++) random.nextDouble() * scale * 3 - scale];

        final range = computeChartRange(values, safeMin: safeMin, safeMax: safeMax);

        final low = [safeMin, ...values].reduce(math.min);
        final high = [safeMax, ...values].reduce(math.max);
        final pad = (high - low) * 0.1;
        final reason = 'values=$values safe=$safeMin..$safeMax -> '
            '${range.minY}..${range.maxY} step ${range.interval}';

        expect(range.interval, greaterThan(0), reason: reason);
        expect(range.minY, lessThanOrEqualTo(low - pad + 1e-9), reason: reason);
        expect(range.maxY, greaterThanOrEqualTo(high + pad - 1e-9), reason: reason);

        final steps = (range.maxY - range.minY) / range.interval;
        expect(steps, inInclusiveRange(2, 9), reason: reason);

        final minTicks = range.minY / range.interval;
        final maxTicks = range.maxY / range.interval;
        expect((minTicks - minTicks.round()).abs(), lessThan(1e-6), reason: reason);
        expect((maxTicks - maxTicks.round()).abs(), lessThan(1e-6), reason: reason);
      }
    });

    test('invariants hold with a floor, which never goes below it', () {
      final random = math.Random(7);
      for (var i = 0; i < 200; i++) {
        final count = 1 + random.nextInt(30);
        final values = [for (var n = 0; n < count; n++) random.nextDouble() * 4500];
        final range = computeChartRange(values, safeMin: 0, safeMax: 500, floor: 0);

        expect(range.minY, 0);
        expect(range.maxY, greaterThanOrEqualTo(values.reduce(math.max)));
        expect(range.maxY, greaterThan(500));
      }
    });
  });
}
