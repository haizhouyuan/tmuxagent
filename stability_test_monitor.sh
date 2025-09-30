#!/bin/bash
# 3-4小时长期稳定性测试监控脚本

echo "=== Orchestrator长期稳定性测试开始 ==="
echo "开始时间: $(date)"
echo "目标运行时长: 4小时 (240分钟)"

# 创建测试日志目录
mkdir -p stability_test_logs

# 每10分钟采集一次关键指标
for i in {1..24}; do  # 24次 * 10分钟 = 4小时
    echo "=== 第${i}次采样 ($(date)) ===" | tee -a stability_test_logs/metrics_$(date +%Y%m%d).log
    
    # 采集Prometheus指标
    curl -s http://localhost:9108/metrics | grep -E "(decision_latency_seconds_count|decision_errors_total|json_parse_failures)" | tee -a stability_test_logs/metrics_$(date +%Y%m%d).log
    
    # 检查服务状态
    ps aux | grep orchestrator | grep -v grep | tee -a stability_test_logs/process_$(date +%Y%m%d).log
    
    # 采集最新决策样例
    tail -5 orchestrator-final-test.log | grep "OrchestratorDecision" | tee -a stability_test_logs/decisions_$(date +%Y%m%d).log
    
    echo "第${i}次采样完成，下次采样时间: $(date -d '+10 minutes')"
    sleep 600  # 等待10分钟
done

echo "=== 长期稳定性测试完成 ===" | tee -a stability_test_logs/summary_$(date +%Y%m%d).log
echo "结束时间: $(date)" | tee -a stability_test_logs/summary_$(date +%Y%m%d).log
