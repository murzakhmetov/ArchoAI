// The background sign-in on the second server: what is sent where, and which
// session is kept. A fake HTTP client stands in for the server; no network.
import 'dart:convert';

import 'package:archoai_app/services/account_mirror.dart';
import 'package:archoai_app/services/backend_config.dart';
import 'package:archoai_app/services/session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

const cloud = BackendEndpoint(
  choice: ServerChoice.fallback,
  url: 'https://abcdefghijklmnop.supabase.co',
  publishableKey: 'cloud-key',
);
const pi = BackendEndpoint(
  choice: ServerChoice.primary,
  url: 'http://192.168.1.61:54321',
  publishableKey: 'pi-key',
);

Map<String, dynamic> sessionJson(String email) => {
      'access_token': 'access-$email',
      'token_type': 'bearer',
      'expires_in': 3600,
      'refresh_token': 'refresh-$email',
      'user': {
        'id': '00000000-0000-0000-0000-000000000001',
        'aud': 'authenticated',
        'email': email,
        'created_at': '2026-11-27T00:00:00Z',
        'app_metadata': <String, dynamic>{},
        'user_metadata': <String, dynamic>{},
      },
    };

http.Response json(int status, Object body) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

final invalidCredentials = json(400, {
  'code': 400,
  'error_code': 'invalid_credentials',
  'msg': 'Invalid login credentials',
});

/// Fake server: answers from [handler] and records every request.
class FakeServer {
  FakeServer(this.handler);
  final Future<http.Response> Function(http.Request request) handler;
  final requests = <http.Request>[];

  http.Client get client => MockClient((request) async {
        requests.add(request);
        return handler(request);
      });
}

Future<SessionStore> storeWith(Map<String, Object> values) async {
  SharedPreferences.setMockInitialValues(values);
  return SessionStore(await SharedPreferences.getInstance());
}

Future<void> mirror(
  FakeServer server,
  SessionStore store, {
  BackendEndpoint target = cloud,
  String email = 'ana@example.org',
  bool Function()? stillWanted,
}) =>
    AccountMirror.mirror(
      target: target,
      email: email,
      password: 'secret-password',
      stillWanted: stillWanted ?? () => true,
      store: store,
      httpClient: server.client,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('signs in on an HTTPS server and keeps the session under its key', () async {
    final server = FakeServer((_) async => json(200, sessionJson('ana@example.org')));
    final store = await storeWith({});
    await mirror(server, store);

    expect(server.requests.single.url.path, '/auth/v1/token');
    expect(server.requests.single.url.queryParameters['grant_type'], 'password');
    expect(store.emailOf(cloud), 'ana@example.org');
    expect(store.prefs.getString(cloud.sessionStorageKey), contains('access-ana@example.org'));
  });

  test('registers when the account is unknown there, without PKCE', () async {
    final server = FakeServer((request) async => request.url.path == '/auth/v1/signup'
        ? json(200, sessionJson('ana@example.org'))
        : invalidCredentials);
    final store = await storeWith({});
    await mirror(server, store);

    expect(server.requests.map((r) => r.url.path), ['/auth/v1/token', '/auth/v1/signup']);
    final signUp = jsonDecode(server.requests.last.body) as Map<String, dynamic>;
    expect(signUp['email'], 'ana@example.org');
    expect(signUp['code_challenge'], isNull, reason: 'PKCE needs storage the mirror does not have');
    expect(store.emailOf(cloud), 'ana@example.org');
  });

  test('never sends a password to a plain-HTTP server', () async {
    final server = FakeServer((_) async => json(200, sessionJson('ana@example.org')));
    final store = await storeWith({});
    await mirror(server, store, target: pi);

    expect(server.requests, isEmpty);
    expect(store.sessionOf(pi), isNull);
    expect(AccountMirror.mayReceivePassword(pi), isFalse);
    expect(AccountMirror.mayReceivePassword(cloud), isTrue);
  });

  test('a sign-out or another sign-in in the meantime wins', () async {
    final server = FakeServer((_) async => json(200, sessionJson('ana@example.org')));
    final store = await storeWith({});
    await mirror(server, store, stillWanted: () => false);

    expect(server.requests, hasLength(1));
    expect(store.sessionOf(cloud), isNull);
  });

  test("somebody else's kept session is removed first, even without a request", () async {
    final server = FakeServer((_) async => json(500, {}));
    final store = await storeWith({pi.sessionStorageKey: jsonEncode(sessionJson('boris@example.org'))});
    await mirror(server, store, target: pi);

    expect(server.requests, isEmpty);
    expect(store.sessionOf(pi), isNull);
  });

  test("the same user's kept session stays when the server cannot be used", () async {
    final server = FakeServer((_) async => json(500, {}));
    final kept = jsonEncode(sessionJson('ana@example.org'));
    final store = await storeWith({pi.sessionStorageKey: kept});
    await mirror(server, store, target: pi, email: 'ANA@example.org');

    expect(store.sessionOf(pi), kept);
  });

  test('a failing server is logged, never thrown', () async {
    final server = FakeServer((_) async => throw http.ClientException('connection refused'));
    final store = await storeWith({});
    await expectLater(mirror(server, store), completes);
    expect(store.sessionOf(cloud), isNull);
  });

  test('a refused sign-in (not an unknown account) does not register', () async {
    final server = FakeServer((_) async => json(400, {'error_code': 'email_not_confirmed', 'msg': 'Email not confirmed'}));
    final store = await storeWith({});
    await mirror(server, store);

    expect(server.requests.map((r) => r.url.path), ['/auth/v1/token']);
    expect(store.sessionOf(cloud), isNull);
  });
}
