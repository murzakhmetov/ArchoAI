import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:intl/intl.dart';
import 'package:shimmer/shimmer.dart';
import '../../config/app_colors.dart';
import '../../config/localization.dart';
import '../../models/sensor_data.dart';
import '../../widgets/glass_card.dart';
import '../../services/ai_service.dart';

class MetricDetailScreen extends StatefulWidget {
  final String title;
  final String unit;
  final IconData icon;
  final Color accentColor;
  final List<SensorData> history;

  const MetricDetailScreen({
    super.key,
    required this.title,
    required this.unit,
    required this.icon,
    required this.accentColor,
    required this.history,
  });

  @override
  State<MetricDetailScreen> createState() => _MetricDetailScreenState();
}

class _MetricDetailScreenState extends State<MetricDetailScreen> {
  final AiService _aiService = AiService();
  String? _aiInsight;
  bool _isLoadingAi = true;

  @override
  void initState() {
    super.initState();
    _fetchAiInsight();
  }

  Future<void> _fetchAiInsight() async {
    setState(() => _isLoadingAi = true);
    
    final Map<String, dynamic> summary = {};
    for (var m in ['Temperature', 'Humidity', 'Air Quality']) {
      final List<double> vals = widget.history.map((d) {
        if (m == 'Temperature') return d.temperature;
        if (m == 'Humidity') return d.humidity;
        return d.airQuality;
      }).toList();
      
      if (vals.isNotEmpty) {
        final avg = vals.reduce((a, b) => a + b) / vals.length;
        final max = vals.reduce((a, b) => a > b ? a : b);
        summary[m] = 'Current: ${vals.last}, Avg: ${avg.toStringAsFixed(1)}, Peak: $max';
      }
    }

    final insight = await _aiService.getComprehensiveInsight(
      targetMetric: widget.title,
      allMetricsSummary: summary,
    );

    if (mounted) {
      setState(() {
        _aiInsight = insight;
        _isLoadingAi = false;
      });
    }
  }

  void _showAiAdviserChat() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => Container(
        height: MediaQuery.of(context).size.height * 0.7,
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(32)),
          border: Border.all(color: widget.accentColor.withValues(alpha: 0.1)),
        ),
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.textTertiary.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 24),
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: widget.accentColor.withValues(alpha: 0.1),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(Icons.psychology_outlined, color: widget.accentColor, size: 28),
                ),
                const SizedBox(width: 16),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      RU.globalAiChat,
                      style: const TextStyle(
                        color: AppColors.textPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 1.5,
                      ),
                    ),
                    const Text(
                      'Gemini 3.1 Flash-Lite Engine',
                      style: TextStyle(
                        color: AppColors.mint,
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 32),
            Expanded(
              child: SingleChildScrollView(
                child: Text(
                  _aiInsight ?? 'Медитация ИИ...',
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 15,
                    height: 1.6,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: () => Navigator.pop(context),
                style: ElevatedButton.styleFrom(
                  backgroundColor: widget.accentColor,
                  foregroundColor: Colors.black,
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  elevation: 0,
                ),
                child: const Text('ПРИНЯТО', style: TextStyle(fontWeight: FontWeight.w800)),
              ),
            ),
            SizedBox(height: MediaQuery.of(context).padding.bottom),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final List<double> values = widget.history.map((d) {
      if (widget.title == RU.temperature) return d.temperature;
      if (widget.title == RU.humidity) return d.humidity;
      return d.airQuality;
    }).toList();

    final double currentVal = values.isNotEmpty ? values.last : 0;
    final double avg = values.isEmpty ? 0 : values.reduce((a, b) => a + b) / values.length;
    final double max = values.isEmpty ? 0 : values.reduce((a, b) => a > b ? a : b);
    final double min = values.isEmpty ? 0 : values.reduce((a, b) => a < b ? a : b);

    final double safeMin = _getSafeMin();
    final double safeMax = _getSafeMax();
    final bool isCurrentOutside = currentVal < safeMin || currentVal > safeMax;
    final String statusText = _getStatusText(currentVal, safeMin, safeMax);
    final Color statusColor = isCurrentOutside ? AppColors.amber : AppColors.mint;

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new_rounded, color: AppColors.textPrimary, size: 20),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(
          widget.title.toUpperCase(),
          style: const TextStyle(
            color: AppColors.textPrimary,
            fontSize: 14,
            fontWeight: FontWeight.w700,
            letterSpacing: 2,
          ),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 20),
            GlassCard(
              padding: const EdgeInsets.all(24),
              child: Column(
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: widget.accentColor.withValues(alpha: 0.1),
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Icon(widget.icon, color: widget.accentColor, size: 24),
                      ),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              RU.currentReading,
                              style: TextStyle(
                                color: AppColors.textSecondary.withValues(alpha: 0.6),
                                fontSize: 10,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 1,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              statusText.toUpperCase(),
                              style: TextStyle(
                                color: statusColor,
                                fontSize: 13,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      Text(
                        currentVal.toStringAsFixed(1),
                        style: const TextStyle(
                          color: AppColors.textPrimary,
                          fontSize: 48,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(width: 4),
                      Text(
                        widget.unit,
                        style: TextStyle(
                          color: widget.accentColor,
                          fontSize: 24,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 32),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  RU.trend24h,
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 1.5,
                  ),
                ),
                Text(
                  '${RU.safeZone}: $safeMin - $safeMax ${widget.unit}',
                  style: TextStyle(
                    color: AppColors.textTertiary.withValues(alpha: 0.5),
                    fontSize: 10,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            GlassCard(
              padding: const EdgeInsets.fromLTRB(8, 24, 16, 16),
              child: SizedBox(
                height: 280, // Increased height for better visibility
                child: LineChart(
                  LineChartData(
                    minY: _getMinY(),
                    maxY: _getMaxY(),
                    lineTouchData: LineTouchData(
                      touchTooltipData: LineTouchTooltipData(
                        getTooltipColor: (touchedSpot) => AppColors.surface.withValues(alpha: 0.95),
                        tooltipRoundedRadius: 12,
                        tooltipPadding: const EdgeInsets.all(12),
                        getTooltipItems: (touchedSpots) {
                          return touchedSpots.map((spot) {
                            return LineTooltipItem(
                              '${spot.y.toStringAsFixed(1)} ${widget.unit}\n',
                              const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.bold, fontSize: 13),
                              children: [
                                TextSpan(
                                  text: DateFormat('HH:mm').format(widget.history[spot.x.toInt()].createdAt),
                                  style: TextStyle(color: widget.accentColor, fontWeight: FontWeight.normal, fontSize: 11),
                                ),
                              ],
                            );
                          }).toList();
                        },
                      ),
                      handleBuiltInTouches: true,
                    ),
                    gridData: FlGridData(
                      show: true,
                      drawVerticalLine: false,
                      horizontalInterval: _getYInterval(),
                      getDrawingHorizontalLine: (value) => FlLine(
                        color: AppColors.textTertiary.withValues(alpha: 0.03),
                        strokeWidth: 1,
                      ),
                    ),
                    titlesData: FlTitlesData(
                      show: true,
                      rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                      topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                      bottomTitles: AxisTitles(
                        sideTitles: SideTitles(
                          showTitles: true,
                          reservedSize: 34,
                          interval: 1,
                          getTitlesWidget: (value, meta) {
                            final index = value.toInt();
                            if (index < 0 || index >= widget.history.length) return const SizedBox();
                            
                            final Set<int> targetIndices = {
                              0, 
                              (widget.history.length / 4).floor(), 
                              (widget.history.length / 2).floor(), 
                              (3 * widget.history.length / 4).floor(), 
                              widget.history.length - 1
                            };
                            if (!targetIndices.contains(index)) return const SizedBox();
                            
                            String label;
                            if (index == 0) {
                              label = '-24ч';
                            } else if (index == (widget.history.length / 4).floor()) {
                              label = '-18ч';
                            } else if (index == (widget.history.length / 2).floor()) {
                              label = '-12ч';
                            } else if (index == (3 * widget.history.length / 4).floor()) {
                              label = '-6ч';
                            } else {
                              label = 'СЕЙЧАС';
                            }

                            return SideTitleWidget(
                              meta: meta,
                              space: 12,
                              child: Text(
                                label,
                                style: TextStyle(
                                  color: AppColors.textTertiary.withValues(alpha: 0.4),
                                  fontSize: 8,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                      leftTitles: AxisTitles(
                        sideTitles: SideTitles(
                          showTitles: true,
                          interval: _getYInterval(),
                          reservedSize: 38,
                          getTitlesWidget: (value, meta) => SideTitleWidget(
                            meta: meta,
                            space: 8,
                            child: Text(
                              value.toInt().toString(),
                              style: TextStyle(color: AppColors.textTertiary.withValues(alpha: 0.4), fontSize: 9, fontWeight: FontWeight.bold),
                            ),
                          ),
                        ),
                      ),
                    ),
                    extraLinesData: ExtraLinesData(
                      horizontalLines: [
                        HorizontalLine(
                          y: safeMin,
                          color: AppColors.mint.withValues(alpha: 0.2),
                          strokeWidth: 1.5,
                          dashArray: [8, 4],
                        ),
                        HorizontalLine(
                          y: safeMax,
                          color: AppColors.mint.withValues(alpha: 0.2),
                          strokeWidth: 1.5,
                          dashArray: [8, 4],
                        ),
                      ],
                    ),
                    borderData: FlBorderData(show: false),
                    lineBarsData: [
                      LineChartBarData(
                        spots: values
                            .asMap()
                            .entries
                            .map((e) => FlSpot(e.key.toDouble(), e.value))
                            .toList(),
                        isCurved: true,
                        curveSmoothness: 0.35,
                        color: widget.accentColor,
                        barWidth: 4,
                        isStrokeCapRound: true,
                        dotData: FlDotData(
                          show: true,
                          getDotPainter: (spot, percent, barData, index) {
                            if (index == barData.spots.length - 1) {
                              return FlDotCirclePainter(
                                radius: 6,
                                color: widget.accentColor,
                                strokeWidth: 3,
                                strokeColor: Colors.white,
                              );
                            }
                            return FlDotCirclePainter(radius: 0);
                          },
                        ),
                        shadow: Shadow(
                          color: widget.accentColor.withValues(alpha: 0.5),
                          blurRadius: 10,
                          offset: const Offset(0, 5),
                        ),
                        belowBarData: BarAreaData(
                          show: true,
                          gradient: LinearGradient(
                            colors: [
                              widget.accentColor.withValues(alpha: 0.25),
                              widget.accentColor.withValues(alpha: 0.05),
                              widget.accentColor.withValues(alpha: 0.0),
                            ],
                            begin: Alignment.topCenter,
                            end: Alignment.bottomCenter,
                          ),
                        ),
                      ),
                    ],
                  ),
                  duration: const Duration(milliseconds: 1000),
                  curve: Curves.easeInOutCubic,
                ),
              ),
            ),
            const SizedBox(height: 32),
            GestureDetector(
              onTap: _showAiAdviserChat,
              child: GlassCard(
                padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 24),
                child: Row(
                  children: [
                    Shimmer.fromColors(
                      baseColor: widget.accentColor,
                      highlightColor: Colors.white,
                      child: const Icon(Icons.psychology_rounded, size: 24),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            RU.askAi,
                            style: const TextStyle(
                              color: AppColors.textPrimary,
                              fontSize: 13,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 1,
                            ),
                          ),
                          const Text(
                            'Глубокий анализ сохранности',
                            style: TextStyle(
                              color: AppColors.textSecondary,
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Icon(Icons.arrow_forward_ios_rounded, color: widget.accentColor, size: 14),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 32),
            Row(
              children: [
                Expanded(child: _buildStatItem(RU.average, avg, safeMin, safeMax, Icons.analytics_outlined)),
                const SizedBox(width: 12),
                Expanded(child: _buildStatItem(RU.peak, max, safeMin, safeMax, Icons.trending_up_rounded)),
                const SizedBox(width: 12),
                Expanded(child: _buildStatItem(RU.lowest, min, safeMin, safeMax, Icons.trending_down_rounded)),
              ],
            ),
            const SizedBox(height: 32),
            Text(
              RU.dynamicInsight,
              style: const TextStyle(
                color: AppColors.textSecondary,
                fontSize: 10,
                fontWeight: FontWeight.w600,
                letterSpacing: 2,
              ),
            ),
            const SizedBox(height: 16),
            GlassCard(
              padding: const EdgeInsets.all(20),
              child: _isLoadingAi 
                ? _buildShimmerLoading()
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(Icons.auto_awesome_rounded, color: widget.accentColor, size: 18),
                          const SizedBox(width: 10),
                          const Text(
                            'Анализ Gemini',
                            style: TextStyle(
                              color: AppColors.textPrimary,
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Text(
                        _aiInsight ?? 'Готов к анализу...',
                        style: const TextStyle(
                          color: AppColors.textSecondary,
                          fontSize: 13,
                          height: 1.6,
                        ),
                      ),
                    ],
                  ),
            ),
            const SizedBox(height: 48),
          ],
        ),
      ),
    );
  }

  Widget _buildShimmerLoading() {
    return Shimmer.fromColors(
      baseColor: AppColors.textTertiary.withValues(alpha: 0.1),
      highlightColor: AppColors.textTertiary.withValues(alpha: 0.2),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(width: 150, height: 16, color: Colors.white),
        const SizedBox(height: 12),
        Container(width: double.infinity, height: 12, color: Colors.white),
        const SizedBox(height: 8),
        Container(width: 250, height: 12, color: Colors.white),
      ]),
    );
  }

  Widget _buildStatItem(String label, double val, double min, double max, IconData icon) {
    final bool isOutside = val < min || val > max;
    final Color valueColor = isOutside ? AppColors.amber : AppColors.textPrimary;
    return GlassCard(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: AppColors.textTertiary.withValues(alpha: 0.4), size: 14),
          const SizedBox(height: 8),
          Text(val.toStringAsFixed(1), style: TextStyle(color: valueColor, fontSize: 18, fontWeight: FontWeight.w800)),
          const SizedBox(height: 2),
          Text(label, style: TextStyle(color: AppColors.textSecondary.withValues(alpha: 0.5), fontSize: 8, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  double _getSafeMin() => widget.title == RU.temperature ? 18 : (widget.title == RU.humidity ? 40 : 0);
  double _getSafeMax() => widget.title == RU.temperature ? 22 : (widget.title == RU.humidity ? 50 : 500);
  double _getMinY() => widget.title == RU.temperature ? 10 : (widget.title == RU.humidity ? 20 : 300);
  double _getMaxY() => widget.title == RU.temperature ? 30 : (widget.title == RU.humidity ? 70 : 600);
  double _getYInterval() => widget.title == RU.temperature ? 5 : (widget.title == RU.humidity ? 10 : 50);

  String _getStatusText(double val, double min, double max) {
    if (val < min) return RU.belowRecommended;
    if (val > max) return RU.aboveRecommended;
    return RU.optimalRange;
  }
}
