import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'config/app_colors.dart';
import 'config/app_theme.dart';
import 'screens/auth/auth_gate.dart';

import 'services/backend_service.dart';
import 'services/language_service.dart';
import 'widgets/server_picker.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Set preferred orientations
  await SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);

  // Picks the server (cloud or Raspberry Pi, see services/backend_config.dart)
  // and initializes Supabase for it.
  final envError = await _loadEnv();
  if (envError == null) await BackendService.instance.initialize(dotenv.env);

  // Set system UI overlay style for dark theme
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Color(0xFF14141F),
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  runApp(RootApp(envError: envError));
}

/// Loads `.env`. Returns a human readable error, or null when it is loaded.
Future<String?> _loadEnv() async {
  try {
    await dotenv.load(fileName: '.env');
    return null;
  } catch (e) {
    debugPrint('CRITICAL INITIALIZATION ERROR: $e');
    return 'Could not load the .env file.\n'
        'Copy archoai_app/.env.example to archoai_app/.env and fill in your keys.\n\n$e';
  }
}

/// Shows the app on the server in use, a "connecting" screen while the server
/// is switched, or a readable configuration error. Without Supabase every
/// screen would crash on `Supabase.instance`, so the app is only built once a
/// server is connected, and rebuilt from scratch after every switch.
class RootApp extends StatelessWidget {
  const RootApp({super.key, this.envError});

  final String? envError;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: BackendService.instance,
      builder: (context, _) {
        final backend = BackendService.instance;
        if (backend.isSwitching) return const ConnectingApp();
        final error = envError ?? backend.initError;
        if (error != null || backend.active == null) {
          return StartupErrorApp(message: error ?? 'Not connected to a server.');
        }
        return ArchoAIApp(key: ValueKey(backend.generation));
      },
    );
  }
}

class ArchoAIApp extends StatelessWidget {
  const ArchoAIApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: LanguageService.instance,
      builder: (context, _) {
        return MaterialApp(
          title: 'ArchoAI',
          debugShowCheckedModeBanner: false,
          theme: AppTheme.darkTheme,
          locale: LanguageService.instance.locale,
          home: const AuthGate(),
        );
      },
    );
  }
}

/// Shown for the moment the app reconnects to another server.
class ConnectingApp extends StatelessWidget {
  const ConnectingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'ArchoAI',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: const Scaffold(
        backgroundColor: AppColors.background,
        body: Center(child: CircularProgressIndicator(color: AppColors.cyan)),
      ),
    );
  }
}

/// Shown instead of the app when configuration or backend setup failed.
class StartupErrorApp extends StatelessWidget {
  const StartupErrorApp({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'ArchoAI',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: Scaffold(
        backgroundColor: AppColors.background,
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.settings_suggest_outlined,
                      color: AppColors.amber, size: 40),
                  const SizedBox(height: 16),
                  const Text(
                    'ArchoAI is not configured',
                    style: TextStyle(
                      color: AppColors.textPrimary,
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    message,
                    style: const TextStyle(
                      color: AppColors.textSecondary,
                      fontSize: 13,
                      height: 1.4,
                    ),
                  ),
                  // With two servers the user can try the other one from here.
                  const StartupServerButtons(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
