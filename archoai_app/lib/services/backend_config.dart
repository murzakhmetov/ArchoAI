/// Which servers the app can use and how it picks one. Pure Dart (no Flutter),
/// so the rules are unit tested; scripts/scripts/backend_select.py implements
/// the same rules for the Python scanner scripts.
///
/// `.env` names (all optional except the first server):
///   SUPABASE_URL (or SUPABASE_PROJECT_REF)  first server, as before
///   SUPABASE_PUBLISHABLE_KEY / SUPABASE_ANON_KEY / SUPABASE_KEY  its key
///   SUPABASE_FALLBACK_URL + SUPABASE_FALLBACK_KEY  second server
///   SUPABASE_SERVER = auto (default) | primary | fallback
///
/// With two servers, `auto` uses the first one when it answers and the second
/// one otherwise. Each client writes to ONE server; server/replicate.py on the
/// Raspberry Pi copies the data between the two (see server/README.md).
library;

/// What kind of server an address points at.
enum BackendKind { cloud, selfHosted }

/// Which server the user wants. Stored as its [name].
enum ServerChoice { auto, primary, fallback }

class BackendConfigException implements Exception {
  const BackendConfigException(this.message);
  final String message;

  @override
  String toString() => message;
}

/// One Supabase server: the cloud project or the stack on the Raspberry Pi.
class BackendEndpoint {
  const BackendEndpoint({
    required this.choice,
    required this.url,
    required this.publishableKey,
  });

  /// [ServerChoice.primary] or [ServerChoice.fallback].
  final ServerChoice choice;
  final String url;
  final String publishableKey;

  Uri get uri => Uri.parse(url);
  String get host => uri.host;

  BackendKind get kind => isCloudUrl(url) ? BackendKind.cloud : BackendKind.selfHosted;

  /// Where the signed-in session of THIS server is kept on the device. Each
  /// server has its own accounts, so the sessions must never share a key
  /// (supabase_flutter's default uses only the first host label, which every
  /// 192.168.x.x address would share). The path counts too: two servers can
  /// sit behind one reverse proxy.
  String get sessionStorageKey {
    final path = uri.path.replaceAll(RegExp(r'[^A-Za-z0-9]+'), '_');
    return 'sb-archoai-$host-${uri.port}$path-auth-token';
  }

  /// The key supabase_flutter used before the servers had their own keys.
  String get legacySessionStorageKey => 'sb-${host.split('.').first}-auth-token';

  @override
  bool operator ==(Object other) =>
      other is BackendEndpoint &&
      other.choice == choice &&
      other.url == url &&
      other.publishableKey == publishableKey;

  @override
  int get hashCode => Object.hash(choice, url, publishableKey);

  @override
  String toString() => 'BackendEndpoint(${choice.name}, $url)';
}

class BackendConfig {
  const BackendConfig({
    required this.primary,
    this.fallback,
    this.defaultChoice = ServerChoice.auto,
    this.warnings = const [],
  });

  /// Reads the `.env` values. Throws [BackendConfigException] when the first
  /// server is missing; a half-configured second server is ignored with a
  /// warning, so it can never break a setup that worked before.
  factory BackendConfig.fromEnv(Map<String, String> env) {
    String? read(String key) {
      final value = env[key]?.trim();
      return (value == null || value.isEmpty) ? null : value;
    }

    final projectRef = read('SUPABASE_PROJECT_REF');
    final url = read('SUPABASE_URL') ?? (projectRef == null ? null : 'https://$projectRef.supabase.co');
    final key = read('SUPABASE_PUBLISHABLE_KEY') ?? read('SUPABASE_ANON_KEY') ?? read('SUPABASE_KEY');
    if (url == null) {
      throw const BackendConfigException('Missing SUPABASE_URL (or SUPABASE_PROJECT_REF) in .env.');
    }
    if (key == null) {
      throw const BackendConfigException('Missing SUPABASE_ANON_KEY (or SUPABASE_PUBLISHABLE_KEY) in .env.');
    }
    final primary = BackendEndpoint(choice: ServerChoice.primary, url: normalizeUrl(url), publishableKey: key);

    final warnings = <String>[];
    final keys = {for (final name in keyNames) ?read(name)};
    if (keys.length > 1) {
      warnings.add('${keyNames.join(', ')} hold different keys; the first one set is used.');
    }
    final fallback = _readFallback(read, primary, warnings);
    final rawChoice = read('SUPABASE_SERVER');
    final choice = parseServerChoice(rawChoice);
    if (rawChoice != null && choice.name != rawChoice.toLowerCase()) {
      warnings.add('Unknown SUPABASE_SERVER "$rawChoice", using auto.');
    }
    return BackendConfig(primary: primary, fallback: fallback, defaultChoice: choice, warnings: List.unmodifiable(warnings));
  }

  static BackendEndpoint? _readFallback(
    String? Function(String) read,
    BackendEndpoint primary,
    List<String> warnings,
  ) {
    final url = read('SUPABASE_FALLBACK_URL');
    final key = read('SUPABASE_FALLBACK_KEY');
    if (url == null) {
      if (key != null) warnings.add('SUPABASE_FALLBACK_KEY is set without SUPABASE_FALLBACK_URL; ignored.');
      return null;
    }
    if (key == null) {
      warnings.add('SUPABASE_FALLBACK_URL is set without SUPABASE_FALLBACK_KEY; the second server is ignored.');
      return null;
    }
    final normalized = normalizeUrl(url);
    if (!isUsableServerUrl(normalized)) {
      warnings.add('SUPABASE_FALLBACK_URL "$url" is not a usable address; ignored.');
      return null;
    }
    if (normalized == primary.url) {
      warnings.add('SUPABASE_FALLBACK_URL is the same server as SUPABASE_URL; ignored.');
      return null;
    }
    return BackendEndpoint(choice: ServerChoice.fallback, url: normalized, publishableKey: key);
  }

  /// Names of the first server's key, in the order the app reads them. The
  /// scripts use the same order whenever a second server is configured.
  static const keyNames = ['SUPABASE_PUBLISHABLE_KEY', 'SUPABASE_ANON_KEY', 'SUPABASE_KEY'];

  final BackendEndpoint primary;
  final BackendEndpoint? fallback;

  /// SUPABASE_SERVER from `.env`; a choice saved on the device wins over it.
  final ServerChoice defaultChoice;
  final List<String> warnings;

  bool get hasFallback => fallback != null;

  List<BackendEndpoint> get endpoints => [primary, ?fallback];

  /// The server other than [endpoint], if there is one.
  BackendEndpoint? otherThan(BackendEndpoint endpoint) =>
      endpoints.where((e) => e.choice != endpoint.choice).firstOrNull;
}

ServerChoice parseServerChoice(String? value) {
  final normalized = value?.trim().toLowerCase();
  return ServerChoice.values.where((c) => c.name == normalized).firstOrNull ?? ServerChoice.auto;
}

final RegExp _projectRef = RegExp(r'^[A-Za-z0-9][A-Za-z0-9-]{0,62}$');

/// Trims the address, lowercases the scheme and drops trailing slashes, so
/// `$url/rest/v1` is valid. A bare cloud project ref becomes its cloud URL.
/// Same results as normalize_base_url in scripts/scripts/supabase_models.py
/// for every usable address.
String normalizeUrl(String url) {
  final trimmed = url.trim();
  final schemeEnd = trimmed.indexOf('://');
  if (schemeEnd < 0) {
    return _projectRef.hasMatch(trimmed) ? 'https://$trimmed.supabase.co' : trimmed;
  }
  final lowerScheme = trimmed.substring(0, schemeEnd).toLowerCase() + trimmed.substring(schemeEnd);
  return lowerScheme.replaceFirst(RegExp(r'/+$'), '');
}

/// An http(s) address with a host and no credentials, query or fragment: the
/// same addresses scripts/scripts/supabase_models.py accepts for a server.
bool isUsableServerUrl(String url) {
  final uri = Uri.tryParse(url);
  return uri != null &&
      (uri.scheme == 'http' || uri.scheme == 'https') &&
      uri.host.isNotEmpty &&
      uri.userInfo.isEmpty &&
      !uri.hasQuery &&
      !uri.hasFragment &&
      !url.contains('#');
}

bool isCloudUrl(String url) {
  final host = Uri.tryParse(url.trim())?.host.toLowerCase() ?? '';
  return host.endsWith('.supabase.co') || host.endsWith('.supabase.in');
}

/// Picks the server to connect to.
///
/// A forced choice is used as it is (no network). `auto` with a single server
/// uses it without probing. `auto` with two servers asks both at once and
/// takes the first server when it answers, else the second when it answers,
/// else the first (the screens then show their usual connection errors).
Future<BackendEndpoint> selectEndpoint(
  BackendConfig config,
  ServerChoice choice,
  Future<bool> Function(BackendEndpoint endpoint) isReachable,
) async {
  final fallback = config.fallback;
  switch (choice) {
    case ServerChoice.primary:
      return config.primary;
    case ServerChoice.fallback:
      return fallback ?? config.primary;
    case ServerChoice.auto:
      if (fallback == null) return config.primary;
      // A probe that throws counts as "not reachable"; the unawaited one must
      // not surface as an unhandled error either.
      Future<bool> probe(BackendEndpoint e) => isReachable(e).catchError((Object _) => false);
      final primaryUp = probe(config.primary);
      final fallbackUp = probe(fallback);
      if (await primaryUp) return config.primary;
      if (await fallbackUp) return fallback;
      return config.primary;
  }
}
