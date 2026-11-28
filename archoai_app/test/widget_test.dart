import 'package:archoai_app/config/app_colors.dart';
import 'package:archoai_app/models/sensor_data.dart';
import 'package:archoai_app/models/sensor_metric.dart';
import 'package:archoai_app/screens/chat/ai_chat_screen.dart';
import 'package:archoai_app/screens/dashboard/dashboard_screen.dart';
import 'package:archoai_app/screens/dashboard/metric_detail_screen.dart';
import 'package:archoai_app/widgets/metric_card.dart';
import 'package:archoai_app/widgets/status_badge.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _host(Widget child) => MaterialApp(home: Scaffold(body: Center(child: child)));

void main() {
  group('StatusBadge', () {
    const styles = <(String, IconData, Color)>[
      ('CRITICAL', Icons.error_outline_rounded, AppColors.red),
      ('WARNING', Icons.warning_amber_rounded, AppColors.amber),
      ('NORMAL', Icons.check_circle_outline_rounded, AppColors.mint),
      ('SOMETHING_ELSE', Icons.info_outline_rounded, AppColors.textSecondary),
    ];

    for (final (status, icon, color) in styles) {
      testWidgets('$status gets its own icon and colour', (tester) async {
        await tester.pumpWidget(_host(StatusBadge(label: 'label', status: status)));

        expect(find.text('LABEL'), findsOneWidget);
        expect(find.byIcon(icon), findsOneWidget);
        expect(tester.widget<Text>(find.text('LABEL')).style?.color, color);
        expect(tester.widget<Icon>(find.byIcon(icon)).color, color);
      });
    }

    testWidgets('status is matched case-insensitively', (tester) async {
      await tester.pumpWidget(_host(const StatusBadge(label: 'hot', status: 'critical')));

      expect(find.byIcon(Icons.error_outline_rounded), findsOneWidget);
    });
  });

  group('MetricCard', () {
    Widget card({String status = 'NORMAL', List<double>? sparkline, VoidCallback? onTap}) {
      return _host(MetricCard(
        title: 'Temperature',
        value: '21.5',
        unit: '°C',
        status: status,
        icon: Icons.thermostat_outlined,
        sparklineData: sparkline,
        onTap: onTap,
      ));
    }

    testWidgets('shows title in upper case, value and unit', (tester) async {
      await tester.pumpWidget(card());

      expect(find.text('TEMPERATURE'), findsOneWidget);
      expect(find.text('21.5'), findsOneWidget);
      expect(find.text('°C'), findsOneWidget);
    });

    testWidgets('shows a status badge only when the status is not NORMAL', (tester) async {
      await tester.pumpWidget(card(status: 'NORMAL'));
      expect(find.byType(StatusBadge), findsNothing);

      await tester.pumpWidget(card(status: 'WARNING'));
      expect(find.byType(StatusBadge), findsOneWidget);
      expect(find.text('WARNING'), findsOneWidget);
    });

    testWidgets('draws a sparkline with MIN and MAX captions from two points up', (tester) async {
      await tester.pumpWidget(card(sparkline: [20.0, 18.5, 22.0]));

      expect(find.byType(LineChart), findsOneWidget);
      expect(find.text('MIN: 18.5'), findsOneWidget);
      expect(find.text('MAX: 22.0'), findsOneWidget);
    });

    testWidgets('has no sparkline without at least two points', (tester) async {
      await tester.pumpWidget(card(sparkline: [20.0]));
      expect(find.byType(LineChart), findsNothing);

      await tester.pumpWidget(card());
      expect(find.byType(LineChart), findsNothing);
    });

    testWidgets('forwards taps to onTap', (tester) async {
      var taps = 0;
      await tester.pumpWidget(card(onTap: () => taps++));

      await tester.tap(find.byType(MetricCard));

      expect(taps, 1);
    });
  });

  // The screens below run without Supabase and without a network: Supabase and the
  // AI client are not initialised, so their calls fail fast and the screens must cope.
  group('screens without a backend', () {
    testWidgets('dashboard shows no verdict while loading, then offline instead of LIVE', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: DashboardScreen()));

      // First load still running.
      expect(find.text('LIVE'), findsNothing);
      expect(find.text('НЕ В СЕТИ'), findsNothing);

      // The load fails because Supabase is not initialised: there is no reading.
      await tester.pump();
      expect(find.text('Ошибка загрузки данных'), findsOneWidget);
      expect(find.text('НЕ В СЕТИ'), findsOneWidget);
      expect(find.text('LIVE'), findsNothing);

      // The 30 s refresh timer must be harmless, also after the screen is gone.
      await tester.pump(const Duration(seconds: 31));
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 31));
    });

    List<SensorData> history(double Function(int i) airQuality) => [
          for (var i = 0; i < 25; i++)
            SensorData(
              id: i,
              temperature: 20.0 + (i % 5),
              humidity: 45.0 + (i % 10),
              airQuality: airQuality(i),
              createdAt: DateTime(2026, 5, 1).add(Duration(hours: i)),
            ),
        ];

    Future<LineChartData> pumpDetail(WidgetTester tester, SensorMetric metric, List<SensorData> data) async {
      await tester.pumpWidget(MaterialApp(
        home: MetricDetailScreen(
          metric: metric,
          history: data,
        ),
      ));
      await tester.pump();
      return tester.widget<LineChart>(find.byType(LineChart)).data;
    }

    void expectAllSpotsVisible(LineChartData chart) {
      for (final spot in chart.lineBarsData.single.spots) {
        expect(spot.y, inInclusiveRange(chart.minY, chart.maxY));
      }
    }

    testWidgets('detail chart: air quality of 125-173 is inside the axis and the chart clips', (tester) async {
      // The old fixed axis (300-600) pushed this data out of the card.
      final chart = await pumpDetail(tester, SensorMetric.airQuality, history((i) => 125.0 + i * 2));

      // The axis covers the data and the 0-1000 ppm safe zone.
      expect(chart.minY, 0);
      expect(chart.maxY, 1200);
      expectAllSpotsVisible(chart);
      expect(chart.clipData.top && chart.clipData.bottom, isTrue);
    });

    testWidgets('detail chart: raw ADC values up to 4080 get a matching axis', (tester) async {
      final chart = await pumpDetail(tester, SensorMetric.airQuality, history((i) => i * 170.0));

      expect(chart.maxY, greaterThanOrEqualTo(4080));
      expectAllSpotsVisible(chart);
    });

    testWidgets('detail chart: temperature axis follows the data and shows the safe zone', (tester) async {
      final chart = await pumpDetail(tester, SensorMetric.airTemperature, history((i) => 400.0));

      expectAllSpotsVisible(chart);
      for (final line in chart.extraLinesData.horizontalLines) {
        expect(line.y, inInclusiveRange(chart.minY, chart.maxY));
      }
    });

    testWidgets('detail chart: soil moisture plots only the rows that have it', (tester) async {
      final data = [
        for (var i = 0; i < 10; i++)
          SensorData(
            id: i,
            temperature: 21,
            humidity: 45,
            airQuality: 300,
            soilMoisture: i < 4 ? null : 30.0 + i, // older rows: no soil sensor yet
            createdAt: DateTime(2026, 5, 1).add(Duration(hours: i)),
          ),
      ];
      final chart = await pumpDetail(tester, SensorMetric.soilMoisture, data);

      expect(chart.lineBarsData.single.spots.map((s) => s.y), [34, 35, 36, 37, 38, 39]);
      expectAllSpotsVisible(chart);
      expect(find.text('39.0'), findsNWidgets(2)); // current reading and peak
      expect(find.text('34.0'), findsOneWidget); // lowest: the rows without a value do not count as 0
    });

    testWidgets('detail chart: air quality uses ppm when the row has it', (tester) async {
      final data = [
        for (var i = 0; i < 5; i++)
          SensorData(
            id: i,
            temperature: 21,
            humidity: 45,
            airQuality: 3000,
            airPpm: 600.0 + i,
            createdAt: DateTime(2026, 5, 1).add(Duration(hours: i)),
          ),
      ];
      final chart = await pumpDetail(tester, SensorMetric.airQuality, data);

      expect(chart.lineBarsData.single.spots.map((s) => s.y), [600, 601, 602, 603, 604]);
    });

    testWidgets('chat: a back button appears only when the screen was pushed', (tester) async {
      await tester.pumpWidget(const MaterialApp(home: AiChatScreen()));
      expect(find.byType(BackButton), findsNothing);

      await tester.pumpWidget(MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(builder: (_) => const AiChatScreen()),
            ),
            child: const Text('open'),
          ),
        ),
      ));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      expect(find.byType(BackButton), findsOneWidget);
    });

    testWidgets('chat: the input is cleared on send and the chat is not locked afterwards', (tester) async {
      Future<void> waitForReply() async {
        for (var i = 0; i < 60 && find.byType(CircularProgressIndicator).evaluate().isNotEmpty; i++) {
          await tester.pump(const Duration(milliseconds: 500));
        }
        expect(find.byType(CircularProgressIndicator), findsNothing);
      }

      await tester.pumpWidget(const MaterialApp(home: AiChatScreen()));

      await tester.enterText(find.byType(TextField), 'first question');
      await tester.tap(find.byIcon(Icons.send_rounded));
      await tester.pump();
      expect(find.text('first question'), findsOneWidget);
      expect(tester.widget<TextField>(find.byType(TextField)).controller!.text, isEmpty);
      await waitForReply();

      // Whatever the AI call returned (here an error text), sending must work again.
      await tester.enterText(find.byType(TextField), 'second question');
      await tester.tap(find.byIcon(Icons.send_rounded));
      await tester.pump();
      expect(find.text('second question'), findsOneWidget);
      await waitForReply();
    });
  });
}
