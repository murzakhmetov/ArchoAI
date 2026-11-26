// One saved session per server, the one-time move from supabase_flutter's old
// key, and local sign-out. SharedPreferences is the in-memory test double.
import 'dart:convert';

import 'package:archoai_app/services/backend_config.dart';
import 'package:archoai_app/services/session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

final config = BackendConfig.fromEnv({
  'SUPABASE_URL': 'http://192.168.1.61:54321',
  'SUPABASE_KEY': 'pi-key',
  'SUPABASE_FALLBACK_URL': 'https://abcdefghijklmnop.supabase.co',
  'SUPABASE_FALLBACK_KEY': 'cloud-key',
});
final pi = config.primary;
final cloud = config.fallback!;

Future<SessionStore> storeWith(Map<String, Object> values) async {
  SharedPreferences.setMockInitialValues(values);
  return SessionStore(await SharedPreferences.getInstance());
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('an old session moves to the server key once', () async {
    final store = await storeWith({'sb-192-auth-token': 'SESSION'});
    await store.migrateLegacy(pi, config);
    expect(store.sessionOf(pi), 'SESSION');
    expect(store.prefs.containsKey('sb-192-auth-token'), isFalse);
  });

  test('a leftover old key is removed when the server key exists', () async {
    final store = await storeWith({'sb-192-auth-token': 'OLD', pi.sessionStorageKey: 'NEW'});
    await store.migrateLegacy(pi, config);
    expect(store.sessionOf(pi), 'NEW');
    expect(store.prefs.containsKey('sb-192-auth-token'), isFalse);
  });

  test('a shared old key belongs to the first server only', () async {
    final twoPis = BackendConfig.fromEnv({
      'SUPABASE_URL': 'http://192.168.1.61:54321',
      'SUPABASE_KEY': 'a',
      'SUPABASE_FALLBACK_URL': 'http://192.168.1.62:54321',
      'SUPABASE_FALLBACK_KEY': 'b',
    });
    final store = await storeWith({'sb-192-auth-token': 'SESSION'});
    await store.migrateLegacy(twoPis.fallback!, twoPis);
    expect(store.sessionOf(twoPis.fallback!), isNull);
    expect(store.prefs.getString('sb-192-auth-token'), 'SESSION');
  });

  test('servers with different old keys each move their own', () async {
    final store = await storeWith({'sb-abcdefghijklmnop-auth-token': 'CLOUD'});
    await store.migrateLegacy(cloud, config);
    expect(store.sessionOf(cloud), 'CLOUD');
    expect(store.sessionOf(pi), isNull);
  });

  test('forget removes the session and the old key, so nothing comes back', () async {
    final store = await storeWith({pi.sessionStorageKey: 'NEW', 'sb-192-auth-token': 'OLD'});
    await store.forget(pi);
    await store.migrateLegacy(pi, config);
    expect(store.sessionOf(pi), isNull);
  });

  test('emailOf reads the user of a kept session', () async {
    final session = jsonEncode({
      'access_token': 't',
      'user': {'id': '1', 'email': 'ana@example.org'},
    });
    final store = await storeWith({pi.sessionStorageKey: session, cloud.sessionStorageKey: 'not json'});
    expect(store.emailOf(pi), 'ana@example.org');
    expect(store.emailOf(cloud), isNull);
  });
}
