import 'package:flutter/material.dart';
import '../../config/app_colors.dart';
import '../../services/supabase_service.dart';
import '../../widgets/glass_card.dart';
import '../../services/language_service.dart';
import '../../config/localization.dart';

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final user = SupabaseService.instance.currentUser;
    final email = user?.email ?? 'Unknown';

    return ListenableBuilder(
      listenable: LanguageService.instance,
      builder: (context, _) => Scaffold(
        body: SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SizedBox(height: 24),

                // Header
                Text(
                  S.current.account.toUpperCase(),
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 2,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  S.current.profile,
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 28,
                    fontWeight: FontWeight.w700,
                  ),
                ),

                const SizedBox(height: 32),

                // Avatar + email
                Center(
                  child: Column(
                    children: [
                      Container(
                        width: 90,
                        height: 90,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: RadialGradient(
                            colors: [
                              AppColors.cyan.withValues(alpha: 0.15),
                              AppColors.surface,
                            ],
                          ),
                          border: Border.all(
                            color: AppColors.cyan.withValues(alpha: 0.3),
                            width: 1.5,
                          ),
                        ),
                        child: Center(
                          child: Text(
                            email.isNotEmpty ? email[0].toUpperCase() : '?',
                            style: const TextStyle(
                              color: AppColors.cyan,
                              fontSize: 36,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        email,
                        style: const TextStyle(
                          color: AppColors.textPrimary,
                          fontSize: 16,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                        decoration: BoxDecoration(
                          color: AppColors.mint.withValues(alpha: 0.1),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          S.current.researcher.toUpperCase(),
                          style: const TextStyle(
                            color: AppColors.mint,
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 1.5,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: 40),

                // Settings
                Text(
                  S.current.settings.toUpperCase(),
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 1.5,
                  ),
                ),
                const SizedBox(height: 12),

                _buildSettingItem(
                  icon: Icons.notifications_none_rounded,
                  title: S.current.notifications,
                  subtitle: LanguageService.instance.isRussian ? 'Оповещения датчиков' : 'Sensor alerts',
                  trailing: Container(
                    width: 44,
                    height: 24,
                    decoration: BoxDecoration(
                      color: AppColors.mint.withValues(alpha: 0.2),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Align(
                      alignment: Alignment.centerRight,
                      child: Container(
                        width: 20,
                        height: 20,
                        margin: const EdgeInsets.only(right: 2),
                        decoration: const BoxDecoration(
                          color: AppColors.mint,
                          shape: BoxShape.circle,
                        ),
                      ),
                    ),
                  ),
                ),
                _buildSettingItem(
                  icon: Icons.language_rounded,
                  title: S.current.language,
                  subtitle: LanguageService.instance.isRussian ? 'Русский' : 'English',
                  onTap: () => LanguageService.instance.toggleLanguage(),
                ),
                _buildSettingItem(
                  icon: Icons.storage_outlined,
                  title: S.current.storage,
                  subtitle: LanguageService.instance.isRussian ? '3D модели' : '3D models',
                ),

                const SizedBox(height: 24),

                // About
                Text(
                  S.current.about.toUpperCase(),
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 1.5,
                  ),
                ),
                const SizedBox(height: 12),

                _buildSettingItem(
                  icon: Icons.info_outline_rounded,
                  title: S.current.version,
                  subtitle: '1.0.0',
                ),
                _buildSettingItem(
                  icon: Icons.description_outlined,
                  title: S.current.terms,
                  subtitle: LanguageService.instance.isRussian ? 'Юридическая информация' : 'Legal info',
                ),
                _buildSettingItem(
                  icon: Icons.shield_outlined,
                  title: S.current.privacy,
                  subtitle: LanguageService.instance.isRussian ? 'Защита данных' : 'Data protection',
                ),

                const SizedBox(height: 32),

                // Logout
                GlassCard(
                  onTap: () async {
                    final confirmed = await showDialog<bool>(
                      context: context,
                      builder: (ctx) => AlertDialog(
                        backgroundColor: AppColors.surface,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(16),
                        ),
                        title: Text(
                          LanguageService.instance.isRussian ? 'Выход' : 'Sign Out',
                          style: const TextStyle(color: AppColors.textPrimary),
                        ),
                        content: Text(
                          S.current.logoutConfirm,
                          style: const TextStyle(color: AppColors.textSecondary),
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.of(ctx).pop(false),
                            child: Text(
                              S.current.cancel,
                              style: const TextStyle(color: AppColors.textSecondary),
                            ),
                          ),
                          TextButton(
                            onPressed: () => Navigator.of(ctx).pop(true),
                            child: Text(
                              LanguageService.instance.isRussian ? 'Выйти' : 'Sign Out',
                              style: const TextStyle(color: AppColors.red),
                            ),
                          ),
                        ],
                      ),
                    );

                    if (confirmed == true) {
                      await SupabaseService.instance.signOut();
                    }
                  },
                  borderColor: AppColors.red.withValues(alpha: 0.2),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        Icons.logout_rounded,
                        color: AppColors.red.withValues(alpha: 0.8),
                        size: 20,
                      ),
                      const SizedBox(width: 10),
                      Text(
                        S.current.logout.toUpperCase(),
                        style: TextStyle(
                          color: AppColors.red.withValues(alpha: 0.8),
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 1,
                        ),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: 100),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSettingItem({
    required IconData icon,
    required String title,
    required String subtitle,
    Widget? trailing,
    VoidCallback? onTap,
  }) {
    return GlassCard(
      onTap: onTap,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      margin: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: AppColors.cyan.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(icon, color: AppColors.textSecondary, size: 20),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: const TextStyle(
                    color: AppColors.textTertiary,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          trailing ??
            const Icon(
              Icons.chevron_right_rounded,
              color: AppColors.textTertiary,
              size: 20,
            ),
        ],
      ),
    );
  }
}
