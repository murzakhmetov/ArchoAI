import 'package:flutter/material.dart';
import '../config/app_colors.dart';

class StatusBadge extends StatelessWidget {
  final String label;
  final String status;

  const StatusBadge({
    super.key,
    required this.label,
    required this.status,
  });

  Color get _backgroundColor {
    switch (status.toUpperCase()) {
      case 'CRITICAL':
        return AppColors.red.withValues(alpha: 0.15);
      case 'WARNING':
        return AppColors.amber.withValues(alpha: 0.15);
      case 'NORMAL':
        return AppColors.mint.withValues(alpha: 0.15);
      default:
        return AppColors.textTertiary.withValues(alpha: 0.15);
    }
  }

  Color get _textColor {
    switch (status.toUpperCase()) {
      case 'CRITICAL':
        return AppColors.red;
      case 'WARNING':
        return AppColors.amber;
      case 'NORMAL':
        return AppColors.mint;
      default:
        return AppColors.textSecondary;
    }
  }

  IconData get _icon {
    switch (status.toUpperCase()) {
      case 'CRITICAL':
        return Icons.error_outline_rounded;
      case 'WARNING':
        return Icons.warning_amber_rounded;
      case 'NORMAL':
        return Icons.check_circle_outline_rounded;
      default:
        return Icons.info_outline_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: _backgroundColor,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: _textColor.withValues(alpha: 0.3),
          width: 0.5,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(_icon, size: 12, color: _textColor),
          const SizedBox(width: 4),
          Text(
            label.toUpperCase(),
            style: TextStyle(
              color: _textColor,
              fontSize: 9,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.0,
            ),
          ),
        ],
      ),
    );
  }
}
