import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../../config/app_colors.dart';
import '../../config/localization.dart';
import '../../models/sensor_data.dart';
import '../../services/supabase_service.dart';
import '../../widgets/metric_card.dart';
import '../chat/ai_chat_screen.dart';
import '../../widgets/glass_card.dart';
import 'metric_detail_screen.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  SensorData? _latestData;
  List<SensorData> _history = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    setState(() => _isLoading = true);
    try {
      final latest = await SupabaseService.instance.getLatestSensorData();
      final history = await SupabaseService.instance.getSensorDataHistory(hours: 24);
      
      if (mounted) {
        setState(() {
          _latestData = latest;
          _history = history;
          _isLoading = false;
        });
        // Start stream only after initial load
        _setupRealtimeStream();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _isLoading = false;
        });
      }
    }
  }

  void _setupRealtimeStream() {
    SupabaseService.instance.streamSensorData().listen((data) {
      if (data.isNotEmpty && mounted) {
        final newLatest = SensorData.fromJson(data.first);
        setState(() {
          // Update latest data
          _latestData = newLatest;
          
          // Only append to history if it's a new record by ID
          if (_history.isEmpty || _history.last.id != newLatest.id) {
            _history.add(newLatest);
            // Keep history limited to last 200 points for performance
            if (_history.length > 200) {
              _history.removeAt(0);
            }
          }
        });
      }
    });
  }

  List<double> _extractSparkline(List<SensorData> data, String field) {
    final values = data.map((d) {
      switch (field) {
        case 'temperature':
          return d.temperature;
        case 'humidity':
          return d.humidity;
        case 'air_quality':
          return d.airQuality;
        default:
          return 0.0;
      }
    }).toList();
    if (values.length > 20) {
      return values.sublist(values.length - 20);
    }
    return values;
  }

  void _navigateToDetail(String title, String unit, IconData icon, Color color) {
    if (_history.isEmpty) return;
    
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => MetricDetailScreen(
          title: title,
          unit: unit,
          icon: icon,
          accentColor: color,
          history: _history,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: _loadData,
          color: AppColors.cyan,
          backgroundColor: AppColors.surface,
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              // Header
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(24, 24, 24, 8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'ARCHO AI',
                                style: TextStyle(
                                  color: AppColors.textPrimary,
                                  fontSize: 20,
                                  fontWeight: FontWeight.w900,
                                  letterSpacing: 2,
                                ),
                              ),
                              Text(
                                RU.dashboard.toUpperCase(),
                                style: TextStyle(
                                  color: AppColors.textSecondary.withValues(alpha: 0.6),
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                  letterSpacing: 1.2,
                                ),
                              ),
                            ],
                          ),
                          Row(
                            children: [
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                                decoration: BoxDecoration(
                                  color: AppColors.mint.withValues(alpha: 0.1),
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(color: AppColors.mint.withValues(alpha: 0.2)),
                                ),
                                child: Row(
                                  children: [
                                    Container(
                                      width: 6,
                                      height: 6,
                                      decoration: const BoxDecoration(color: AppColors.mint, shape: BoxShape.circle),
                                    ),
                                    const SizedBox(width: 6),
                                    const Text('LIVE', style: TextStyle(color: AppColors.mint, fontSize: 10, fontWeight: FontWeight.bold)),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 8),
                              GestureDetector(
                                onTap: () => Navigator.push(
                                  context,
                                  MaterialPageRoute(builder: (context) => const AiChatScreen()),
                                ),
                                child: Container(
                                  padding: const EdgeInsets.all(10),
                                  decoration: BoxDecoration(
                                    color: AppColors.mint.withValues(alpha: 0.1),
                                    shape: BoxShape.circle,
                                    border: Border.all(color: AppColors.mint.withValues(alpha: 0.3)),
                                  ),
                                  child: const Icon(Icons.psychology_rounded, color: AppColors.mint, size: 22),
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                      if (_latestData != null) ...[
                        const SizedBox(height: 12),
                        _buildLastUpdateText(),
                      ],
                    ],
                  ),
                ),
              ),

              // Content
              if (_isLoading)
                const SliverFillRemaining(
                  child: Center(
                    child: CircularProgressIndicator(
                      color: AppColors.cyan,
                      strokeWidth: 2.5,
                    ),
                  ),
                )
              else if (_error != null)
                SliverFillRemaining(
                  child: Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.cloud_off_rounded,
                            color: AppColors.textTertiary,
                            size: 48,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Ошибка загрузки данных',
                            style: TextStyle(
                              color: AppColors.textSecondary,
                              fontSize: 16,
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(height: 8),
                          Text(
                            _error!,
                            style: TextStyle(
                              color: AppColors.textTertiary,
                              fontSize: 12,
                            ),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 24),
                          TextButton.icon(
                            onPressed: _loadData,
                            icon: const Icon(Icons.refresh_rounded),
                            label: const Text('Повторить'),
                            style: TextButton.styleFrom(
                              foregroundColor: AppColors.cyan,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                )
              else if (_latestData == null)
                SliverFillRemaining(
                  child: Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.sensors_off_rounded,
                          color: AppColors.textTertiary,
                          size: 48,
                        ),
                        const SizedBox(height: 16),
                        const Text(
                          'Нет данных с датчиков',
                          style: TextStyle(
                            color: AppColors.textSecondary,
                            fontSize: 16,
                          ),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'Подключите устройство ArchoAI',
                          style: TextStyle(
                            color: AppColors.textTertiary,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                )
              else
                SliverPadding(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  sliver: SliverList(
                    delegate: SliverChildListDelegate([
                      // Temperature card
                      MetricCard(
                        title: RU.temperature,
                        value: _latestData!.temperature.toStringAsFixed(1),
                        unit: '°C',
                        status: _latestData!.temperatureStatus,
                        icon: Icons.thermostat_outlined,
                        accentColor: AppColors.cyan,
                        sparklineData: _extractSparkline(_history, 'temperature'),
                        onTap: () => _navigateToDetail(
                          RU.temperature, '°C', Icons.thermostat_outlined, AppColors.cyan
                        ),
                      ),
                      const SizedBox(height: 12),

                      // Humidity card
                      MetricCard(
                        title: RU.humidity,
                        value: _latestData!.humidity.toStringAsFixed(1),
                        unit: '%',
                        status: _latestData!.humidityStatus,
                        icon: Icons.water_drop_outlined,
                        accentColor: AppColors.mint,
                        sparklineData: _extractSparkline(_history, 'humidity'),
                        onTap: () => _navigateToDetail(
                          RU.humidity, '%', Icons.water_drop_outlined, AppColors.mint
                        ),
                      ),
                      const SizedBox(height: 12),

                      // Air Quality card
                      MetricCard(
                        title: RU.airQuality,
                        value: _latestData!.airQuality.toStringAsFixed(0),
                        unit: 'PPM',
                        status: _latestData!.airQualityStatus,
                        icon: Icons.air_rounded,
                        accentColor: AppColors.amber,
                        sparklineData: _extractSparkline(_history, 'air_quality'),
                        onTap: () => _navigateToDetail(
                          RU.airQuality, 'PPM', Icons.air_rounded, AppColors.amber
                        ),
                      ),
                      const SizedBox(height: 16),

                      // Summary card
                      GlassCard(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'СВОДКА ПО МИКРОКЛИМАТУ',
                              style: TextStyle(
                                color: AppColors.textSecondary,
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 1.5,
                              ),
                            ),
                            const SizedBox(height: 16),
                            _buildSummaryRow(
                              'Условия хранения',
                              _getOverallStatus(),
                              _getOverallStatusColor(),
                            ),
                            const SizedBox(height: 12),
                            _buildSummaryRow(
                              'Точек данных (24ч)',
                              '${_history.length}',
                              AppColors.textPrimary,
                            ),
                            const SizedBox(height: 12),
                            _buildSummaryRow(
                              'Статус устройства',
                              'Подключено',
                              AppColors.mint,
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 100),
                    ]),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildLastUpdateText() {
    if (_latestData == null) return const SizedBox();
    
    final diff = DateTime.now().difference(_latestData!.createdAt);
    String timeText;
    
    if (diff.inSeconds < 60) {
      timeText = 'Только что';
    } else if (diff.inMinutes < 60) {
      timeText = 'Обновлено ${diff.inMinutes} мин назад';
    } else {
      timeText = 'Обновлено в ${DateFormat('HH:mm').format(_latestData!.createdAt)}';
    }

    return Text(
      timeText,
      style: const TextStyle(
        color: AppColors.textTertiary,
        fontSize: 12,
        fontWeight: FontWeight.w500,
      ),
    );
  }

  Widget _buildSummaryRow(String label, String value, Color valueColor) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: AppColors.textSecondary,
            fontSize: 13,
          ),
        ),
        Text(
          value,
          style: TextStyle(
            color: valueColor,
            fontSize: 13,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }

  String _getOverallStatus() {
    if (_latestData == null) return 'Неизвестно';
    final statuses = [
      _latestData!.temperatureStatus,
      _latestData!.humidityStatus,
      _latestData!.airQualityStatus,
    ];
    if (statuses.contains('CRITICAL')) return 'Критично';
    if (statuses.contains('WARNING')) return 'Внимание';
    return 'Оптимально';
  }

  Color _getOverallStatusColor() {
    final status = _getOverallStatus();
    switch (status) {
      case 'Критично':
        return AppColors.red;
      case 'Внимание':
        return AppColors.amber;
      default:
        return AppColors.mint;
    }
  }
}
