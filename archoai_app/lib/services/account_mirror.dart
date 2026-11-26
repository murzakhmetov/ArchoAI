import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';

import 'backend_config.dart';
import 'session_store.dart';

/// Accounts live on each server separately (the replicator copies data, not
/// users). After a sign-in on one server, [mirror] signs in with the same
/// e-mail and password on the other one, registering there when the account
/// does not exist yet, and keeps that session on the device under the other
/// server's key. A later switch to that server then starts signed in.
///
/// Everything here is best effort: a failure is logged and never reaches the
/// sign-in screen, and the password is never stored. The password only goes
/// to HTTPS servers: a plain-HTTP address such as the Pi's 192.168.x.x can
/// belong to anybody on another network (a cafe Wi-Fi, a hotel).
class AccountMirror {
  AccountMirror._();

  static const Duration timeout = Duration(seconds: 10);

  /// Whether a password may be sent to [target] from the background.
  static bool mayReceivePassword(BackendEndpoint target) => target.uri.scheme == 'https';

  /// Signs in on [target] and keeps the session. [stillWanted] is asked right
  /// before saving: a sign-out or another sign-in in the meantime wins.
  static Future<void> mirror({
    required BackendEndpoint target,
    required String email,
    required String password,
    required bool Function() stillWanted,
    SessionStore? store,
    @visibleForTesting http.Client? httpClient,
  }) async {
    SupabaseClient? client;
    try {
      final sessions = store ?? await SessionStore.open();
      // Never leave somebody else's session ready for the next switch.
      if (sessions.emailOf(target) case final kept? when kept.toLowerCase() != email.toLowerCase()) {
        await sessions.forget(target);
      }
      if (!mayReceivePassword(target)) return;
      client = SupabaseClient(
        target.url,
        target.publishableKey,
        httpClient: httpClient,
        // The implicit flow needs no code-verifier storage; PKCE without one
        // fails before anything is sent.
        authOptions: const AuthClientOptions(autoRefreshToken: false, authFlowType: AuthFlowType.implicit),
      );
      final session = await _signInOrRegister(client, email, password);
      if (session == null) {
        debugPrint('Account mirror: no session on ${target.host} (e-mail confirmation needed?)');
        return;
      }
      if (!stillWanted()) return;
      await sessions.save(target, jsonEncode(session.toJson()));
    } catch (e) {
      debugPrint('Account mirror on ${target.host} failed: $e');
    } finally {
      try {
        await client?.dispose();
      } catch (_) {}
    }
  }

  static Future<Session?> _signInOrRegister(
    SupabaseClient client,
    String email,
    String password,
  ) async {
    try {
      final response = await client.auth
          .signInWithPassword(email: email, password: password)
          .timeout(timeout);
      return response.session;
    } on AuthException catch (e) {
      if (!isUnknownAccount(e)) rethrow;
    }
    final response = await client.auth.signUp(email: email, password: password).timeout(timeout);
    return response.session;
  }

  /// Supabase answers "invalid credentials" both for a wrong password and for
  /// an e-mail it does not know. Registering in the wrong-password case is
  /// harmless: the server refuses an e-mail that is already taken.
  @visibleForTesting
  static bool isUnknownAccount(AuthException e) =>
      e.code == 'invalid_credentials' || e.message.toLowerCase().contains('invalid login credentials');
}
