// Rules for choosing the server (cloud or Raspberry Pi). The same table is
// tested for the Python scripts in scripts/scripts/tests/test_backend_select.py.
import 'package:archoai_app/services/account_mirror.dart';
import 'package:archoai_app/services/backend_config.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

const pi = 'http://192.168.1.61:54321';
const cloud = 'https://abcdefghijklmnop.supabase.co';

Map<String, String> bothServers({String? server}) => {
      'SUPABASE_URL': pi,
      'SUPABASE_KEY': 'pi-key',
      'SUPABASE_FALLBACK_URL': cloud,
      'SUPABASE_FALLBACK_KEY': 'cloud-key',
      'SUPABASE_SERVER': ?server,
    };

/// Probe double: answers from [up] and records what was asked.
class FakeProbe {
  FakeProbe(this.up);
  final Set<String> up;
  final asked = <String>[];

  Future<bool> call(BackendEndpoint endpoint) async {
    asked.add(endpoint.url);
    return up.contains(endpoint.url);
  }
}

void main() {
  group('BackendConfig.fromEnv', () {
    test('a single server keeps working exactly as before', () {
      final config = BackendConfig.fromEnv({'SUPABASE_URL': pi, 'SUPABASE_KEY': 'pi-key'});
      expect(config.primary.url, pi);
      expect(config.primary.publishableKey, 'pi-key');
      expect(config.fallback, isNull);
      expect(config.hasFallback, isFalse);
      expect(config.defaultChoice, ServerChoice.auto);
      expect(config.warnings, isEmpty);
    });

    test('SUPABASE_PROJECT_REF builds the cloud address', () {
      final config = BackendConfig.fromEnv({'SUPABASE_PROJECT_REF': 'abcdefghijklmnop', 'SUPABASE_KEY': 'k'});
      expect(config.primary.url, cloud);
      expect(config.primary.kind, BackendKind.cloud);
    });

    test('key names keep their old precedence', () {
      final config = BackendConfig.fromEnv({
        'SUPABASE_URL': pi,
        'SUPABASE_KEY': 'plain',
        'SUPABASE_ANON_KEY': 'anon',
        'SUPABASE_PUBLISHABLE_KEY': 'publishable',
      });
      expect(config.primary.publishableKey, 'publishable');
    });

    test('a missing first server is a readable error', () {
      expect(() => BackendConfig.fromEnv({'SUPABASE_KEY': 'k'}), throwsA(isA<BackendConfigException>()));
      expect(() => BackendConfig.fromEnv({'SUPABASE_URL': pi}), throwsA(isA<BackendConfigException>()));
    });

    test('two servers: first is primary, second is fallback', () {
      final config = BackendConfig.fromEnv(bothServers());
      expect(config.primary.kind, BackendKind.selfHosted);
      expect(config.fallback!.url, cloud);
      expect(config.fallback!.kind, BackendKind.cloud);
      expect(config.endpoints.map((e) => e.choice), [ServerChoice.primary, ServerChoice.fallback]);
      expect(config.otherThan(config.primary), config.fallback);
      expect(config.otherThan(config.fallback!), config.primary);
    });

    test('a fallback without its key is ignored with a warning', () {
      final config = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_FALLBACK_KEY': ''});
      expect(config.fallback, isNull);
      expect(config.warnings.single, contains('SUPABASE_FALLBACK_KEY'));
    });

    test('a fallback key without an address is ignored with a warning', () {
      final config = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_FALLBACK_URL': '  '});
      expect(config.fallback, isNull);
      expect(config.warnings.single, contains('SUPABASE_FALLBACK_URL'));
    });

    test('the same server twice is one server', () {
      final config = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_FALLBACK_URL': '$pi/'});
      expect(config.fallback, isNull);
      expect(config.warnings, isNotEmpty);
    });

    test('values are trimmed and trailing slashes dropped', () {
      final config = BackendConfig.fromEnv({'SUPABASE_URL': '  $pi//  ', 'SUPABASE_KEY': ' k '});
      expect(config.primary.url, pi);
      expect(config.primary.publishableKey, 'k');
    });

    test('addresses are normalized like the scripts do', () {
      expect(normalizeUrl('HTTP://Pi-Server.local:54321//'), 'http://Pi-Server.local:54321');
      expect(normalizeUrl('abcdefghijklmnop'), cloud);
      expect(normalizeUrl('https://example.org/supabase/'), 'https://example.org/supabase');
      final bareRef = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_FALLBACK_URL': 'abcdefghijklmnop'});
      expect(bareRef.fallback!.url, cloud);
      final sameServer = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_FALLBACK_URL': 'HTTP://192.168.1.61:54321'});
      expect(sameServer.fallback, isNull);
    });

    test('an unusable second address is ignored, like the scripts do', () {
      for (final bad in ['ftp://x.example', 'http://user:pw@cloud.example', 'https://cloud.example?x=1', 'http://']) {
        final config = BackendConfig.fromEnv({...bothServers(server: 'fallback'), 'SUPABASE_FALLBACK_URL': bad});
        expect(config.fallback, isNull, reason: bad);
        expect(config.warnings.single, contains('not a usable address'), reason: bad);
      }
    });

    test('two key names with different keys are reported', () {
      final config = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_PUBLISHABLE_KEY': 'stale'});
      expect(config.primary.publishableKey, 'stale');
      expect(config.warnings.single, contains('different keys'));
      final same = BackendConfig.fromEnv({...bothServers(), 'SUPABASE_PUBLISHABLE_KEY': 'pi-key'});
      expect(same.warnings, isEmpty);
    });

    test('SUPABASE_SERVER is parsed, unknown values mean auto', () {
      expect(BackendConfig.fromEnv(bothServers(server: 'fallback')).defaultChoice, ServerChoice.fallback);
      expect(BackendConfig.fromEnv(bothServers(server: ' PRIMARY ')).defaultChoice, ServerChoice.primary);
      final unknown = BackendConfig.fromEnv(bothServers(server: 'pi'));
      expect(unknown.defaultChoice, ServerChoice.auto);
      expect(unknown.warnings.single, contains('SUPABASE_SERVER'));
    });
  });

  group('selectEndpoint', () {
    final config = BackendConfig.fromEnv(bothServers());

    test('auto uses the first server when it answers', () async {
      final probe = FakeProbe({pi, cloud});
      expect((await selectEndpoint(config, ServerChoice.auto, probe.call)).url, pi);
    });

    test('auto falls back to the second server when the first is down', () async {
      final probe = FakeProbe({cloud});
      expect((await selectEndpoint(config, ServerChoice.auto, probe.call)).url, cloud);
    });

    test('auto keeps the first server when both are down', () async {
      final probe = FakeProbe({});
      expect((await selectEndpoint(config, ServerChoice.auto, probe.call)).url, pi);
    });

    test('auto asks both servers at once', () async {
      final probe = FakeProbe({pi});
      await selectEndpoint(config, ServerChoice.auto, probe.call);
      expect(probe.asked, unorderedEquals([pi, cloud]));
    });

    test('a probe that throws counts as down', () async {
      Future<bool> throwing(BackendEndpoint e) async =>
          e.url == pi ? throw Exception('network') : true;
      expect((await selectEndpoint(config, ServerChoice.auto, throwing)).url, cloud);
    });

    test('a forced choice is used without any probe', () async {
      final probe = FakeProbe({});
      expect((await selectEndpoint(config, ServerChoice.fallback, probe.call)).url, cloud);
      expect((await selectEndpoint(config, ServerChoice.primary, probe.call)).url, pi);
      expect(probe.asked, isEmpty);
    });

    test('a single server is used without any probe', () async {
      final single = BackendConfig.fromEnv({'SUPABASE_URL': pi, 'SUPABASE_KEY': 'k'});
      final probe = FakeProbe({});
      expect((await selectEndpoint(single, ServerChoice.auto, probe.call)).url, pi);
      expect((await selectEndpoint(single, ServerChoice.fallback, probe.call)).url, pi);
      expect(probe.asked, isEmpty);
    });
  });

  group('BackendEndpoint', () {
    test('kind comes from the address', () {
      expect(isCloudUrl(cloud), isTrue);
      expect(isCloudUrl('https://ref.supabase.in'), isTrue);
      expect(isCloudUrl(pi), isFalse);
      expect(isCloudUrl('http://supabase.co.evil.example'), isFalse);
    });

    test('every server keeps its session under its own key', () {
      BackendEndpoint at(String url) =>
          BackendEndpoint(choice: ServerChoice.primary, url: url, publishableKey: 'k');
      final keys = {
        at('http://192.168.1.61:54321').sessionStorageKey,
        at('http://192.168.1.62:54321').sessionStorageKey,
        at('http://192.168.1.61:54331').sessionStorageKey,
        at('https://example.org/pi').sessionStorageKey,
        at('https://example.org/cloud').sessionStorageKey,
        at(cloud).sessionStorageKey,
      };
      expect(keys, hasLength(6));
      // supabase_flutter's old default, which the app migrates from once.
      expect(at('http://192.168.1.61:54321').legacySessionStorageKey, 'sb-192-auth-token');
      expect(at(cloud).legacySessionStorageKey, 'sb-abcdefghijklmnop-auth-token');
    });
  });

  group('AccountMirror.isUnknownAccount', () {
    test('only "invalid credentials" leads to a registration', () {
      expect(AccountMirror.isUnknownAccount(const AuthException('Invalid login credentials')), isTrue);
      expect(AccountMirror.isUnknownAccount(const AuthException('x', code: 'invalid_credentials')), isTrue);
      expect(AccountMirror.isUnknownAccount(const AuthException('Email not confirmed')), isFalse);
      expect(AccountMirror.isUnknownAccount(const AuthException('Too many requests', statusCode: '429')), isFalse);
    });
  });
}
