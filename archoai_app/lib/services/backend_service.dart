import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'account_mirror.dart';
import 'backend_config.dart';
import 'session_store.dart';

/// Owns the connection to the active server (cloud or Raspberry Pi) and the
/// switch between them. Listeners are told after every switch; the app then
/// rebuilds its whole tree so every screen and realtime stream uses the new
/// client.
class BackendService extends ChangeNotifier {
  BackendService._();
  static final BackendService instance = BackendService._();

  static const String choicePreferenceKey = 'archoai.server_choice';
  static const Duration probeTimeout = Duration(milliseconds: 2500);

  /// Turned off by tests that must not create accounts on the other server.
  static bool mirrorAccounts = true;

  BackendConfig? _config;
  BackendEndpoint? _active;
  ServerChoice _choice = ServerChoice.auto;
  String? _initError;
  int _generation = 0;
  int _authEpoch = 0;
  bool _busy = false;
  bool _switching = false;

  BackendConfig? get config => _config;

  /// The server the app is connected to; null before a connection succeeded.
  BackendEndpoint? get active => _active;
  ServerChoice get choice => _choice;
  String? get initError => _initError;
  bool get isSwitching => _switching;

  /// Changes on every (re)connection; used as the key of the app's root.
  int get generation => _generation;

  bool get hasFallback => _config?.hasFallback ?? false;

  /// Reads the servers from [env], picks one and connects. Returns a readable
  /// error, or null when the app can start.
  Future<String?> initialize(Map<String, String> env) async {
    try {
      _config = BackendConfig.fromEnv(env);
    } on BackendConfigException catch (e) {
      return _fail(e.message);
    }
    for (final warning in _config!.warnings) {
      debugPrint('Server config: $warning');
    }
    // Every server's old session is moved now, not on its first use, so the
    // account mirror sees (and can drop) a session of another user.
    for (final endpoint in _config!.endpoints) {
      await _migrateLegacySession(endpoint);
    }
    _choice = await _savedChoice() ?? _config!.defaultChoice;
    return _connect(await selectEndpoint(_config!, _choice, isReachable));
  }

  /// Reconnects for [choice] and, when [remember] is true and the connection
  /// works, keeps the choice on this device. Returns a readable error, or null.
  Future<String?> switchTo(ServerChoice choice, {bool remember = true}) async {
    final config = _config;
    if (config == null) return 'The server configuration is not loaded.';
    if (_busy) return 'The server is already being switched.';
    _busy = true;
    try {
      final endpoint = await selectEndpoint(config, choice, isReachable);
      // Same server as now: keep the connection (and its realtime streams).
      if (endpoint == _active && _initError == null) {
        await _keepChoice(choice, remember);
        notifyListeners();
        return null;
      }
      // Let the app replace its screens with the "connecting" screen before
      // the old client is closed, so no screen keeps using it.
      _switching = true;
      notifyListeners();
      await SchedulerBinding.instance.endOfFrame;
      final error = await _disconnect() ?? await _connect(endpoint);
      if (error == null) await _keepChoice(choice, remember);
      return error;
    } finally {
      _busy = false;
      if (_switching) {
        _switching = false;
        notifyListeners();
      }
    }
  }

  /// True when [endpoint] answers an authenticated request in time. A TCP
  /// connect alone is not enough: a wrong key or a stopped API must count as down.
  /// Same headers as supabase_flutter's own requests and the ESP32, which the
  /// Pi's gateway is known to accept (the cloud accepts them too).
  Future<bool> isReachable(BackendEndpoint endpoint) async {
    try {
      final response = await http
          .get(
            Uri.parse('${endpoint.url}/rest/v1/sensor_data?select=id&limit=1'),
            headers: {
              'apikey': endpoint.publishableKey,
              'Authorization': 'Bearer ${endpoint.publishableKey}',
            },
          )
          .timeout(probeTimeout);
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// After a sign-in on the active server, signs in (or registers) on the
  /// other server too, so a later switch finds a session there. Best effort:
  /// never throws and never stores the password (see [AccountMirror]).
  Future<void> mirrorAccount(String email, String password) async {
    final active = _active;
    final other = active == null ? null : _config?.otherThan(active);
    if (other == null || !mirrorAccounts) return;
    final epoch = ++_authEpoch;
    await AccountMirror.mirror(
      target: other,
      email: email,
      password: password,
      stillWanted: () => epoch == _authEpoch && identical(_active, active),
    );
  }

  /// Removes the sessions this device keeps for the servers that are not in
  /// use, and stops a mirror that is still running from saving one.
  Future<void> forgetOtherSessions() async {
    _authEpoch++;
    final active = _active;
    final config = _config;
    if (active == null || config == null) return;
    try {
      final store = await SessionStore.open();
      for (final endpoint in config.endpoints.where((e) => e.choice != active.choice)) {
        await store.forget(endpoint);
      }
    } catch (e) {
      debugPrint('Could not remove the sessions of the other servers: $e');
    }
  }

  Future<String?> _connect(BackendEndpoint endpoint) async {
    try {
      // After a failed start or close the old instance may still be marked as
      // initialized; initialize() would then quietly keep that client.
      if (_initError != null) await _disposeQuietly();
      await _migrateLegacySession(endpoint);
      await Supabase.initialize(
        url: endpoint.url,
        publishableKey: endpoint.publishableKey,
        authOptions: FlutterAuthClientOptions(
          localStorage: SharedPreferencesLocalStorage(persistSessionKey: endpoint.sessionStorageKey),
        ),
      );
    } catch (e) {
      debugPrint('CRITICAL INITIALIZATION ERROR: $e');
      // supabase_flutter marks itself initialized before its auth part is
      // ready; without this a retry would silently keep the broken client.
      await _disposeQuietly();
      return _fail('Could not initialize Supabase.\n\n$e');
    }
    // initialize() returns the existing client when the old one could not be
    // closed; never label that client with the new server.
    if (Supabase.instance.client.rest.url != '${endpoint.url}/rest/v1') {
      return _fail('The previous server connection could not be closed. Please restart the app.');
    }
    _active = endpoint;
    _initError = null;
    _generation++;
    debugPrint('Server: connected to ${endpoint.url} (${_choice.name})');
    notifyListeners();
    return null;
  }

  /// Closes the current client. Returns a readable error when that failed.
  Future<String?> _disconnect() async {
    if (_active == null) return null;
    _active = null;
    try {
      await Supabase.instance.dispose();
      return null;
    } catch (e) {
      debugPrint('Could not close the connection: $e');
      return _fail('Could not close the connection to the previous server.\n\n$e');
    }
  }

  Future<void> _disposeQuietly() async {
    try {
      await Supabase.instance.dispose();
    } catch (_) {}
  }

  String _fail(String message) {
    _initError = message;
    notifyListeners();
    return message;
  }

  Future<void> _migrateLegacySession(BackendEndpoint endpoint) async {
    final config = _config;
    if (config == null) return;
    try {
      await (await SessionStore.open()).migrateLegacy(endpoint, config);
    } catch (e) {
      debugPrint('Could not move the saved session: $e');
    }
  }

  Future<ServerChoice?> _savedChoice() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(choicePreferenceKey);
      return saved == null ? null : parseServerChoice(saved);
    } catch (e) {
      debugPrint('Could not read the saved server choice: $e');
      return null;
    }
  }

  /// [remember] false (the startup retry buttons) changes the choice for this
  /// run only, so one retry never turns off Auto for good.
  Future<void> _keepChoice(ServerChoice choice, bool remember) async {
    _choice = choice;
    if (!remember) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(choicePreferenceKey, choice.name);
    } catch (e) {
      debugPrint('Could not save the server choice: $e');
    }
  }
}
