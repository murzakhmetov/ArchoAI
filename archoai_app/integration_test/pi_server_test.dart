// End-to-end run of the real app against the ArchoAI server on the Raspberry Pi
// (the self-hosted Supabase stack that replaced the cloud project).
//
// It drives the UI like a user: registers a new account, waits for the
// dashboard, posts a sensor reading exactly like the ESP32 does and expects
// it on the dashboard through realtime, then opens the collection and expects
// the artifacts stored on the Pi.
//
// On the Pi it runs as a Linux desktop app (the same Dart code as the phone
// build) on a virtual display; server/tests/run_app_e2e.sh runs it and removes
// the test account and the test reading afterwards:
//
//   bash server/tests/run_app_e2e.sh
import 'dart:convert';
import 'dart:math';

import 'package:archoai_app/main.dart' as app;
import 'package:archoai_app/services/backend_config.dart';
import 'package:archoai_app/services/backend_service.dart';
import 'package:archoai_app/services/supabase_service.dart';
import 'package:archoai_app/widgets/metric_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:integration_test/integration_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// Signature of the test reading that run_app_e2e.sh removes afterwards; no real sensor reports
/// exactly these two values. The temperature is random and is what the test looks for on screen.
const double testHumidity = 33.25;
const double testAirQuality = 3999;

Future<void> pumpUntil(
  WidgetTester tester,
  bool Function() condition, {
  Duration timeout = const Duration(seconds: 45),
  String? what,
}) async {
  final end = DateTime.now().add(timeout);
  while (!condition()) {
    if (DateTime.now().isAfter(end)) {
      fail('Timed out after ${timeout.inSeconds} s waiting for ${what ?? 'the UI'}');
    }
    await tester.pump(const Duration(milliseconds: 200));
  }
}

bool shows(Finder finder) => finder.evaluate().isNotEmpty;

/// Scrolls [finder] into view first: on a small window the form continues below the fold.
Future<void> tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump(const Duration(milliseconds: 300));
  await tester.tap(finder);
  await tester.pump(const Duration(milliseconds: 300));
}

Future<void> typeInto(WidgetTester tester, Finder field, String text) async {
  await tester.ensureVisible(field);
  await tester.pump(const Duration(milliseconds: 200));
  await tester.enterText(field, text);
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('register, live dashboard and collection on the Pi server', (tester) async {
    // The test account must not be mirrored to a second server: the cleanup
    // (run_app_e2e.sh) only removes it from the Pi.
    BackendService.mirrorAccounts = false;
    await app.main();
    final signInButton = find.text('SIGN IN');
    final dashboardTab = find.text('ДАШБОРД');
    await pumpUntil(tester, () => shows(signInButton) || shows(dashboardTab),
        what: 'the login screen (is the server configured in archoai_app/.env?)');

    final serverUrl = dotenv.env['SUPABASE_URL']!.trim();
    final publishableKey = dotenv.env['SUPABASE_KEY']!.trim();
    expect(serverUrl, isNot(contains('supabase.co')), reason: 'the app must use the Pi, not the cloud');
    // With a second server configured the app may fail over; this test is about the Pi.
    expect(BackendService.instance.active?.url, normalizeUrl(serverUrl),
        reason: 'the app connected to another server than SUPABASE_URL (is the Pi reachable?)');

    // A session left by an earlier run is closed first.
    if (Supabase.instance.client.auth.currentSession != null) {
      await SupabaseService.instance.signOut();
      await pumpUntil(tester, () => shows(signInButton), what: 'the login screen after sign-out');
    }

    // --- register through the UI ------------------------------------------
    await tapVisible(tester, find.textContaining("Don't have an account?", findRichText: true));
    await pumpUntil(tester, () => shows(find.text('CREATE ACCOUNT')), what: 'the register screen');
    await tester.pump(const Duration(seconds: 1)); // let the page transition finish
    final stamp = DateTime.now().millisecondsSinceEpoch;
    final email = 'selftest-app-$stamp@archoai.test';
    final password = 'Pi-${Random.secure().nextInt(1 << 31)}-$stamp';
    await typeInto(tester, find.widgetWithText(TextFormField, 'Email address').last, email);
    await typeInto(tester, find.widgetWithText(TextFormField, 'Password').last, password);
    await typeInto(tester, find.widgetWithText(TextFormField, 'Confirm password').last, password);
    await tapVisible(tester, find.text('CREATE ACCOUNT').first);
    await pumpUntil(tester, () => shows(dashboardTab), what: 'the dashboard after registration');
    expect(Supabase.instance.client.auth.currentUser?.email, email);
    expect(shows(find.text('Account created! You are signed in.')), isTrue,
        reason: 'the Pi signs new users in at once; no e-mail is sent');

    // --- dashboard: initial load, then a live reading over realtime --------
    await pumpUntil(tester, () => shows(find.text('Повторить')) || shows(find.byType(MetricCard)),
        what: 'the latest sensor reading on the dashboard');
    expect(shows(find.text('Повторить')), isFalse, reason: 'the dashboard reported a loading error');

    // Temperature card: the first card, always built (later cards may be below the fold).
    double randomTemperature() => 10 + Random().nextInt(500) / 10; // 10.0 .. 59.9, one decimal
    var temperature = randomTemperature();
    while (shows(find.text(temperature.toStringAsFixed(1)))) {
      temperature = randomTemperature(); // must not be on screen yet
    }
    final marker = temperature.toStringAsFixed(1);
    // Give the realtime channel a moment to join before the reading arrives.
    await tester.pump(const Duration(seconds: 3));
    final posted = await http.post(
      Uri.parse('$serverUrl/rest/v1/sensor_data'),
      headers: {
        'apikey': publishableKey,
        'Authorization': 'Bearer $publishableKey',
        'Content-Type': 'application/json',
      },
      body: jsonEncode({'temperature': temperature, 'humidity': testHumidity, 'air_quality': testAirQuality}),
    );
    expect(posted.statusCode, anyOf(200, 201), reason: posted.body);
    await pumpUntil(tester, () => shows(find.text(marker)),
        what: 'the new reading ($marker °C) on the dashboard via realtime');

    // --- collection: the artifacts stored on the Pi -------------------------
    final stored = await Supabase.instance.client
        .from('artifacts')
        .select('name')
        .order('created_at', ascending: false)
        .limit(3);
    expect(stored, isNotEmpty, reason: 'the Pi should hold the imported artifacts');
    final names = [for (final row in stored) row['name'] as String];
    await tapVisible(tester, find.text('АРТЕФАКТЫ'));
    await pumpUntil(tester, () => names.any((name) => shows(find.text(name))),
        what: 'one of the newest artifacts ($names) in the collection');

    await SupabaseService.instance.signOut();
    await pumpUntil(tester, () => shows(signInButton), what: 'the login screen after sign-out');
  });
}
