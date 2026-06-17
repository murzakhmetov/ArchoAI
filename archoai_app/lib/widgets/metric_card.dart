import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import '../config/app_colors.dart';
import 'glass_card.dart';
import 'status_badge.dart';

class MetricCard extends StatelessWidget {
  final String title;
  final String value;
  final String unit;
  final String status;
  final IconData icon;
  final Color? accentColor;
  final List<double>? sparklineData;
  final VoidCallback? onTap;

  const MetricCard({
    super.key,
    required this.title,
    required this.value,
    required this.unit,
    required this.status,
    required this.icon,
    this.accentColor,
    this.sparklineData,
    this.onTap,
  });

  Color get _accent => accentColor ?? AppColors.cyan;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: GlassCard(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header row
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: _accent.withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Icon(icon, color: _accent, size: 18),
                    ),
                    const SizedBox(width: 10),
                    Text(
                      title.toUpperCase(),
                      style: TextStyle(
                        color: AppColors.textPrimary.withValues(alpha: 0.8),
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 1.5,
                      ),
                    ),
                  ],
                ),
                if (status.toUpperCase() != 'NORMAL')
                  StatusBadge(label: status, status: status),
              ],
            ),
            const SizedBox(height: 16),

            // Big value
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(
                  value,
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 44,
                    fontWeight: FontWeight.w700,
                    height: 1.0,
                    letterSpacing: -1,
                  ),
                ),
                const SizedBox(width: 2),
                Text(
                  unit,
                  style: TextStyle(
                    color: _accent.withValues(alpha: 0.9),
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),

            // Sparkline chart
            if (sparklineData != null && sparklineData!.length > 1) ...[
              const SizedBox(height: 16),
              Column(
                children: [
                  SizedBox(
                    height: 50,
                    child: LineChart(
                      LineChartData(
                        gridData: const FlGridData(show: false),
                        titlesData: const FlTitlesData(show: false),
                        borderData: FlBorderData(show: false),
                        lineTouchData: const LineTouchData(enabled: false),
                        clipData: const FlClipData.all(),
                        lineBarsData: [
                          LineChartBarData(
                            spots: sparklineData!
                                .asMap()
                                .entries
                                .map((e) => FlSpot(e.key.toDouble(), e.value))
                                .toList(),
                            isCurved: true,
                            curveSmoothness: 0.35,
                            color: _accent,
                            barWidth: 2,
                            isStrokeCapRound: true,
                            dotData: FlDotData(
                              show: true,
                              getDotPainter: (spot, percent, bar, index) {
                                if (index == sparklineData!.length - 1) {
                                  return FlDotCirclePainter(
                                    radius: 3,
                                    color: _accent,
                                    strokeWidth: 1.5,
                                    strokeColor: AppColors.surface,
                                  );
                                }
                                return FlDotCirclePainter(
                                  radius: 0,
                                  color: Colors.transparent,
                                  strokeWidth: 0,
                                  strokeColor: Colors.transparent,
                                );
                              },
                            ),
                            belowBarData: BarAreaData(
                              show: true,
                              gradient: LinearGradient(
                                colors: [
                                  _accent.withValues(alpha: 0.2),
                                  _accent.withValues(alpha: 0.0),
                                ],
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        'MIN: ${sparklineData!.reduce((a, b) => a < b ? a : b).toStringAsFixed(1)}',
                        style: TextStyle(
                          color: AppColors.textTertiary.withValues(alpha: 0.5),
                          fontSize: 8,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.5,
                        ),
                      ),
                      Text(
                        'MAX: ${sparklineData!.reduce((a, b) => a > b ? a : b).toStringAsFixed(1)}',
                        style: TextStyle(
                          color: AppColors.textTertiary.withValues(alpha: 0.5),
                          fontSize: 8,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.5,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
