import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'config/app_colors.dart';
import 'config/app_theme.dart';
import 'screens/auth/auth_gate.dart';

import 'services/language_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Set preferred orientations
  await SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);

  final initError = await _initializeBackend();

  // Set system UI overlay style for dark theme
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Color(0xFF14141F),
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  // Without Supabase every screen would crash on `Supabase.instance`, so show
  // a readable configuration error instead of a red error screen.
  runApp(initError == null
      ? const ArchoAIApp()
      : StartupErrorApp(message: initError));
}

/// Loads `.env` and initializes Supabase. Returns a human readable error
/// message, or null when everything is ready.
Future<String?> _initializeBackend() async {
  try {
    await dotenv.load(fileName: '.env');
  } catch (e) {
    debugPrint('CRITICAL INITIALIZATION ERROR: $e');
    return 'Could not load the .env file.\n'
        'Copy archoai_app/.env.example to archoai_app/.env and fill in your keys.\n\n$e';
  }

  String? env(String key) {
    final value = dotenv.env[key]?.trim();
    return (value == null || value.isEmpty) ? null : value;
  }

  final projectRef = env('SUPABASE_PROJECT_REF');
  final supabaseUrl = env('SUPABASE_URL') ??
      (projectRef == null ? null : 'https://$projectRef.supabase.co');
  final supabaseKey = env('SUPABASE_PUBLISHABLE_KEY') ??
      env('SUPABASE_ANON_KEY') ??
      env('SUPABASE_KEY');

  if (supabaseUrl == null) {
    return 'Missing SUPABASE_URL (or SUPABASE_PROJECT_REF) in .env.';
  }
  if (supabaseKey == null) {
    return 'Missing SUPABASE_ANON_KEY (or SUPABASE_PUBLISHABLE_KEY) in .env.';
  }

  try {
    await Supabase.initialize(
      url: supabaseUrl,
      publishableKey: supabaseKey,
    );
  } catch (e) {
    debugPrint('CRITICAL INITIALIZATION ERROR: $e');
    return 'Could not initialize Supabase.\n\n$e';
  }
  return null;
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
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
