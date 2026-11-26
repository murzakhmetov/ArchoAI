import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'backend_config.dart';

/// The signed-in sessions this device keeps, one per server (see
/// [BackendEndpoint.sessionStorageKey]). supabase_flutter reads and writes the
/// session of the server in use through the same SharedPreferences keys.
class SessionStore {
  const SessionStore(this.prefs);

  final SharedPreferences prefs;

  static Future<SessionStore> open() async => SessionStore(await SharedPreferences.getInstance());

  String? sessionOf(BackendEndpoint endpoint) => prefs.getString(endpoint.sessionStorageKey);

  Future<void> save(BackendEndpoint endpoint, String sessionJson) =>
      prefs.setString(endpoint.sessionStorageKey, sessionJson);

  /// E-mail of the user whose session is kept for [endpoint], if readable.
  String? emailOf(BackendEndpoint endpoint) {
    final stored = sessionOf(endpoint);
    if (stored == null) return null;
    try {
      final user = (jsonDecode(stored) as Map<String, dynamic>)['user'];
      return user is Map ? user['email'] as String? : null;
    } catch (_) {
      return null;
    }
  }

  /// Local sign-out of [endpoint]: its session and any session under the old
  /// supabase_flutter key, which [migrateLegacy] would otherwise bring back.
  Future<void> forget(BackendEndpoint endpoint) async {
    await prefs.remove(endpoint.sessionStorageKey);
    if (endpoint.legacySessionStorageKey != endpoint.sessionStorageKey) {
      await prefs.remove(endpoint.legacySessionStorageKey);
    }
  }

  /// Sessions saved before each server had its own key live under
  /// supabase_flutter's default key. Moves such a session once, so updating
  /// the app signs nobody out. Two servers can share the old key (every
  /// 192.168.x.x address does); it then belongs to the first server only.
  Future<void> migrateLegacy(BackendEndpoint endpoint, BackendConfig config) async {
    final legacy = endpoint.legacySessionStorageKey;
    final current = endpoint.sessionStorageKey;
    if (legacy == current) return;
    if (endpoint.choice != ServerChoice.primary && legacy == config.primary.legacySessionStorageKey) return;
    if (prefs.containsKey(current)) {
      await prefs.remove(legacy); // already moved: a leftover would come back after a sign-out
      return;
    }
    final session = prefs.getString(legacy);
    if (session == null) return;
    await prefs.setString(current, session);
    await prefs.remove(legacy);
  }
}
