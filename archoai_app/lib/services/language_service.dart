import 'package:flutter/material.dart';

class LanguageService extends ChangeNotifier {
  LanguageService._();
  static final LanguageService instance = LanguageService._();

  Locale _locale = const Locale('ru');
  Locale get locale => _locale;

  void setLocale(Locale newLocale) {
    if (_locale == newLocale) return;
    _locale = newLocale;
    notifyListeners();
  }

  bool get isRussian => _locale.languageCode == 'ru';

  void toggleLanguage() {
    setLocale(isRussian ? const Locale('en') : const Locale('ru'));
  }
}
