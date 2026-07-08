"""
Performance Benchmarking Framework for TEAM-GRADE Pipeline

Benchmarks each of the 9 pipeline stages:
1. metadata_stage - Initialize video document (~100ms)
2. download_stage - YouTube download (~5-60s depending on video)
3. frame_extraction_stage - Extract frames at 15 FPS (~30-120s)
4. pose_stage - MediaPipe pose detection (~2-10s per 100 frames)
5. torso_crop_stage - Region extraction (~100-500ms)
6. jersey_ocr_stage - Jersey number detection (~1-5s)
7. rep_extraction_stage - Segmentation (~500ms-2s)
8. biomechanics_stage - Trait scoring (~1-10s)
9. complete_stage - Finalization (~50ms)

Usage:
    python -m ingest.performance.benchmark --video-id test_123456789ab --profile --report
"""

import time
import json
import logging
import statistics
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import argparse

import psutil
import tracemalloc

logger = logging.getLogger(__name__)


@dataclass
class StagePerformance:
    """Performance metrics for a single stage run."""
    stage_name: str
    run_number: int
    duration_ms: float
    memory_used_mb: float
    memory_peak_mb: float
    cpu_percent: float
    timestamp: str
    success: bool
    error_message: Optional[str] = None


@dataclass
class AggregatedMetrics:
    """Aggregated performance metrics across runs."""
    stage_name: str
    runs: int
    total_duration_ms: float
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    stdev_ms: float
    avg_memory_mb: float
    peak_memory_mb: float
    avg_cpu_percent: float
    success_rate: float = 100.0


class PerformanceProfiler:
    """Profiles performance metrics for pipeline stages."""
    
    def __init__(self, stage_name: str):
        self.stage_name = stage_name
        self.start_time = None
        self.start_memory = None
        self.process = psutil.Process()
        self.memory_trace = None
        
    def __enter__(self):
        """Start profiling."""
        self.start_time = time.perf_counter()
        self.start_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        tracemalloc.start()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop profiling and return metrics."""
        duration = (time.perf_counter() - self.start_time) * 1000  # ms
        end_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        memory_used = end_memory - self.start_memory
        
        current, peak = tracemalloc.get_traced_memory()
        peak_memory = peak / 1024 / 1024  # MB
        tracemalloc.stop()
        
        cpu_percent = self.process.cpu_percent(interval=None)
        
        return {
            'duration_ms': duration,
            'memory_used_mb': memory_used,
            'memory_peak_mb': peak_memory,
            'cpu_percent': cpu_percent
        }


class StageBenchmark:
    """Benchmarks individual pipeline stages."""
    
    def __init__(self, num_runs: int = 3):
        self.num_runs = num_runs
        self.results: Dict[str, List[StagePerformance]] = {}
        
    def benchmark_stage(
        self,
        stage_name: str,
        stage_func,
        *args,
        **kwargs
    ) -> List[StagePerformance]:
        """Benchmark a stage function multiple times.
        
        Args:
            stage_name: Name of the stage
            stage_func: Callable to benchmark
            *args: Position arguments for stage_func
            **kwargs: Keyword arguments for stage_func
            
        Returns:
            List of performance metrics for each run
        """
        metrics = []
        
        logger.info(f"Benchmarking {stage_name} ({self.num_runs} runs)...")
        
        for run in range(1, self.num_runs + 1):
            try:
                with PerformanceProfiler(stage_name) as profiler:
                    result = stage_func(*args, **kwargs)
                    profile_data = profiler.__exit__(None, None, None)
                
                perf = StagePerformance(
                    stage_name=stage_name,
                    run_number=run,
                    duration_ms=profile_data['duration_ms'],
                    memory_used_mb=profile_data['memory_used_mb'],
                    memory_peak_mb=profile_data['memory_peak_mb'],
                    cpu_percent=profile_data['cpu_percent'],
                    timestamp=datetime.now().isoformat(),
                    success=True
                )
                
                logger.info(
                    f"  Run {run}: {perf.duration_ms:.2f}ms, "
                    f"Mem: {perf.memory_used_mb:.2f}MB, "
                    f"CPU: {perf.cpu_percent:.1f}%"
                )
                
            except Exception as e:
                perf = StagePerformance(
                    stage_name=stage_name,
                    run_number=run,
                    duration_ms=0,
                    memory_used_mb=0,
                    memory_peak_mb=0,
                    cpu_percent=0,
                    timestamp=datetime.now().isoformat(),
                    success=False,
                    error_message=str(e)
                )
                
                logger.error(f"  Run {run}: FAILED - {e}")
            
            metrics.append(perf)
        
        self.results[stage_name] = metrics
        return metrics
    
    def aggregate_results(self) -> Dict[str, AggregatedMetrics]:
        """Aggregate results across all runs."""
        aggregated = {}
        
        for stage_name, runs in self.results.items():
            successful_runs = [r for r in runs if r.success]
            
            if not successful_runs:
                aggregated[stage_name] = AggregatedMetrics(
                    stage_name=stage_name,
                    runs=len(runs),
                    total_duration_ms=0,
                    avg_duration_ms=0,
                    min_duration_ms=0,
                    max_duration_ms=0,
                    stdev_ms=0,
                    avg_memory_mb=0,
                    peak_memory_mb=0,
                    avg_cpu_percent=0,
                    success_rate=0
                )
                continue
            
            durations = [r.duration_ms for r in successful_runs]
            memories = [r.memory_used_mb for r in successful_runs]
            cpus = [r.cpu_percent for r in successful_runs]
            
            aggregated[stage_name] = AggregatedMetrics(
                stage_name=stage_name,
                runs=len(runs),
                total_duration_ms=sum(durations),
                avg_duration_ms=statistics.mean(durations),
                min_duration_ms=min(durations),
                max_duration_ms=max(durations),
                stdev_ms=statistics.stdev(durations) if len(durations) > 1 else 0,
                avg_memory_mb=statistics.mean(memories),
                peak_memory_mb=max([r.memory_peak_mb for r in successful_runs]),
                avg_cpu_percent=statistics.mean(cpus),
                success_rate=(len(successful_runs) / len(runs)) * 100
            )
        
        return aggregated


class PerformanceReporter:
    """Generates performance reports."""
    
    def __init__(self, results: Dict[str, AggregatedMetrics]):
        self.results = results
        
    def generate_text_report(self) -> str:
        """Generate human-readable text report."""
        lines = [
            "=" * 80,
            "TEAM-GRADE PIPELINE PERFORMANCE REPORT",
            f"Generated: {datetime.now().isoformat()}",
            "=" * 80,
            "",
        ]
        
        # Summary table
        lines.append("STAGE PERFORMANCE SUMMARY")
        lines.append("-" * 80)
        lines.append(
            f"{'Stage':<20} {'Avg (ms)':<12} {'Min/Max (ms)':<20} "
            f"{'Mem (MB)':<12} {'Success':<10}"
        )
        lines.append("-" * 80)
        
        for stage_name, metrics in sorted(self.results.items()):
            if metrics.success_rate > 0:
                avg_fmt = f"{metrics.avg_duration_ms:.2f}"
                minmax_fmt = f"{metrics.min_duration_ms:.2f}/{metrics.max_duration_ms:.2f}"
                mem_fmt = f"{metrics.avg_memory_mb:.2f}"
                success_fmt = f"{metrics.success_rate:.0f}%"
            else:
                avg_fmt = "FAILED"
                minmax_fmt = "-"
                mem_fmt = "-"
                success_fmt = "0%"
            
            lines.append(
                f"{stage_name:<20} {avg_fmt:<12} {minmax_fmt:<20} "
                f"{mem_fmt:<12} {success_fmt:<10}"
            )
        
        lines.extend([
            "-" * 80,
            "",
            "DETAILED METRICS",
            "-" * 80,
        ])
        
        for stage_name, metrics in sorted(self.results.items()):
            lines.extend([
                f"\n{stage_name.upper()}",
                f"  Runs: {metrics.runs}",
                f"  Duration: {metrics.avg_duration_ms:.2f}ms "
                f"(min: {metrics.min_duration_ms:.2f}ms, "
                f"max: {metrics.max_duration_ms:.2f}ms, "
                f"σ: {metrics.stdev_ms:.2f}ms)",
                f"  Memory: {metrics.avg_memory_mb:.2f}MB avg, "
                f"{metrics.peak_memory_mb:.2f}MB peak",
                f"  CPU: {metrics.avg_cpu_percent:.1f}%",
                f"  Success Rate: {metrics.success_rate:.0f}%",
            ])
        
        lines.extend([
            "",
            "=" * 80,
        ])
        
        return "\n".join(lines)
    
    def generate_json_report(self) -> str:
        """Generate JSON report."""
        data = {
            'timestamp': datetime.now().isoformat(),
            'stages': {
                stage: asdict(metrics)
                for stage, metrics in self.results.items()
            }
        }
        return json.dumps(data, indent=2)
    
    def generate_markdown_report(self) -> str:
        """Generate Markdown report."""
        lines = [
            "# TEAM-GRADE Pipeline Performance Report",
            "",
            f"**Generated:** {datetime.now().isoformat()}",
            "",
            "## Summary",
            "",
            "| Stage | Avg Duration | Min/Max | Memory | Success |",
            "|-------|-------------|---------|--------|---------|",
        ]
        
        for stage_name, metrics in sorted(self.results.items()):
            if metrics.success_rate > 0:
                duration = f"{metrics.avg_duration_ms:.2f}ms"
                minmax = f"{metrics.min_duration_ms:.2f}/{metrics.max_duration_ms:.2f}ms"
                memory = f"{metrics.avg_memory_mb:.2f}MB"
                success = f"{metrics.success_rate:.0f}%"
            else:
                duration = "FAILED"
                minmax = "-"
                memory = "-"
                success = "0%"
            
            lines.append(
                f"| {stage_name} | {duration} | {minmax} | {memory} | {success} |"
            )
        
        lines.extend([
            "",
            "## Detailed Analysis",
            "",
        ])
        
        for stage_name, metrics in sorted(self.results.items()):
            lines.extend([
                f"### {stage_name}",
                "",
                f"- **Execution Time:** {metrics.avg_duration_ms:.2f}ms "
                f"(σ={metrics.stdev_ms:.2f}ms)",
                f"- **Memory Usage:** {metrics.avg_memory_mb:.2f}MB "
                f"(peak: {metrics.peak_memory_mb:.2f}MB)",
                f"- **CPU Usage:** {metrics.avg_cpu_percent:.1f}%",
                f"- **Success Rate:** {metrics.success_rate:.0f}%",
                f"- **Runs:** {metrics.runs}",
                "",
            ])
        
        return "\n".join(lines)


def main():
    """Main benchmark runner."""
    parser = argparse.ArgumentParser(description="Benchmark TEAM-GRADE pipeline stages")
    parser.add_argument('--stages', nargs='+', help='Specific stages to benchmark')
    parser.add_argument('--runs', type=int, default=3, help='Number of runs per stage')
    parser.add_argument('--output', help='Output file for results')
    parser.add_argument('--format', choices=['text', 'json', 'markdown'], default='text',
                       help='Output format')
    parser.add_argument('--video-id', help='Test video ID')
    parser.add_argument('--profile', action='store_true', help='Enable profiling')
    
    args = parser.parse_args()
    
    # TODO: Integrate with actual pipeline stages
    logger.info("Benchmark framework ready for integration with pipeline")
    
    return 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    exit(main())
