import 'package:flutter/material.dart';

class AppColors {
  AppColors._();

  // ── Backgrounds ──
  static const Color background = Color(0xFF0A0A0F);
  static const Color surface = Color(0xFF14141F);
  static const Color surfaceLight = Color(0xFF1A1A2E);
  static const Color cardBorder = Color(0x661E1E2E);

  // ── Accent ──
  static const Color cyan = Color(0xFF00E5FF);
  static const Color mint = Color(0xFF00F5A0);
  static const Color cyanDark = Color(0xFF0091A1);
  static const Color mintDark = Color(0xFF009B66);

  // ── Signal ──
  static const Color amber = Color(0xFFFFB547);
  static const Color red = Color(0xFFFF4757);
  static const Color green = Color(0xFF2ED573);

  // ── Text ──
  static const Color textPrimary = Color(0xFFFFFFFF);
  static const Color textSecondary = Color(0xFF6B7280);
  static const Color textTertiary = Color(0xFF4B5563);

  // ── Gradients ──
  static const LinearGradient accentGradient = LinearGradient(
    colors: [cyan, mint],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  static const LinearGradient cardGradient = LinearGradient(
    colors: [Color(0xFF14141F), Color(0xFF0F0F1A)],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  static const LinearGradient cyanGlow = LinearGradient(
    colors: [Color(0x4000E5FF), Color(0x0000E5FF)],
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
  );

  static const LinearGradient mintGlow = LinearGradient(
    colors: [Color(0x4000F5A0), Color(0x0000F5A0)],
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
  );
}
