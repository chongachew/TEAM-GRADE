"""
Optimization Analysis Tools

Analyzes performance bottlenecks and provides optimization recommendations
for each pipeline stage.
"""

import json
import statistics
from dataclasses import dataclass
from typing import Dict, List, Tuple
from enum import Enum


class OptimizationType(Enum):
    """Types of optimizations."""
    CACHING = "caching"
    BATCHING = "batching"
    PARALLELIZATION = "parallelization"
    ALGORITHM = "algorithm"
    RESOURCE = "resource"
    IO = "io"
    MEMORY = "memory"


@dataclass
class OptimizationRecommendation:
    """Single optimization recommendation."""
    stage: str
    optimization_type: OptimizationType
    title: str
    description: str
    expected_improvement_percent: float
    difficulty: str  # easy, medium, hard
    priority: str  # low, medium, high, critical
    implementation_time_hours: float
    risk_level: str  # low, medium, high


class PerformanceAnalyzer:
    """Analyzes performance metrics and identifies optimizations."""
    
    # Expected baseline performance for each stage (in milliseconds)
    BASELINE_PERFORMANCE = {
        'metadata_stage': 100,
        'download_stage': 30000,  # Highly variable, network-dependent
        'frame_extraction_stage': 60000,  # 30-120s typical
        'pose_stage': 5000,  # Per batch of 100 frames
        'torso_crop_stage': 300,
        'jersey_ocr_stage': 2000,
        'rep_extraction_stage': 1000,
        'biomechanics_stage': 5000,
        'complete_stage': 50,
    }
    
    # Performance thresholds for warning/critical
    WARNING_THRESHOLD = 1.2  # 20% above baseline
    CRITICAL_THRESHOLD = 1.5  # 50% above baseline
    
    def __init__(self):
        self.recommendations: Dict[str, List[OptimizationRecommendation]] = {}
    
    def analyze_metrics(self, metrics: Dict) -> Dict:
        """Analyze performance metrics and identify issues."""
        analysis = {}
        
        for stage_name, stage_metrics in metrics.items():
            avg_duration = stage_metrics.get('avg_duration_ms', 0)
            baseline = self.BASELINE_PERFORMANCE.get(stage_name, avg_duration)
            ratio = avg_duration / baseline if baseline > 0 else 0
            
            status = 'normal'
            if ratio > self.CRITICAL_THRESHOLD:
                status = 'critical'
            elif ratio > self.WARNING_THRESHOLD:
                status = 'warning'
            
            analysis[stage_name] = {
                'baseline_ms': baseline,
                'actual_ms': avg_duration,
                'ratio': ratio,
                'status': status,
                'deviation_percent': ((ratio - 1) * 100) if ratio > 1 else 0,
            }
        
        return analysis
    
    def get_recommendations_for_stage(self, stage: str) -> List[OptimizationRecommendation]:
        """Get optimization recommendations for a specific stage."""
        
        recommendations = {
            'metadata_stage': [
                OptimizationRecommendation(
                    stage='metadata_stage',
                    optimization_type=OptimizationType.CACHING,
                    title="Implement metadata caching",
                    description="Cache frequently accessed metadata to reduce Firestore reads",
                    expected_improvement_percent=15,
                    difficulty='easy',
                    priority='low',
                    implementation_time_hours=2,
                    risk_level='low',
                ),
            ],
            
            'download_stage': [
                OptimizationRecommendation(
                    stage='download_stage',
                    optimization_type=OptimizationType.IO,
                    title="Use adaptive bitrate selection",
                    description="Detect network speed and adjust video quality for faster downloads",
                    expected_improvement_percent=25,
                    difficulty='medium',
                    priority='high',
                    implementation_time_hours=6,
                    risk_level='medium',
                ),
                OptimizationRecommendation(
                    stage='download_stage',
                    optimization_type=OptimizationType.RESOURCE,
                    title="Implement parallel chunk downloads",
                    description="Download video in parallel chunks to improve throughput",
                    expected_improvement_percent=40,
                    difficulty='hard',
                    priority='high',
                    implementation_time_hours=8,
                    risk_level='medium',
                ),
            ],
            
            'frame_extraction_stage': [
                OptimizationRecommendation(
                    stage='frame_extraction_stage',
                    optimization_type=OptimizationType.BATCHING,
                    title="Batch frame extraction operations",
                    description="Process multiple frames per FFmpeg invocation instead of one-by-one",
                    expected_improvement_percent=30,
                    difficulty='medium',
                    priority='high',
                    implementation_time_hours=4,
                    risk_level='low',
                ),
                OptimizationRecommendation(
                    stage='frame_extraction_stage',
                    optimization_type=OptimizationType.RESOURCE,
                    title="Use GPU-accelerated video decoding",
                    description="Leverage NVIDIA NVDEC or AMD VCN for hardware video decoding",
                    expected_improvement_percent=50,
                    difficulty='hard',
                    priority='medium',
                    implementation_time_hours=8,
                    risk_level='high',
                ),
            ],
            
            'pose_stage': [
                OptimizationRecommendation(
                    stage='pose_stage',
                    optimization_type=OptimizationType.BATCHING,
                    title="Increase batch size for pose estimation",
                    description="Process more frames per batch (currently optimized but can test larger batches)",
                    expected_improvement_percent=20,
                    difficulty='easy',
                    priority='medium',
                    implementation_time_hours=1,
                    risk_level='low',
                ),
                OptimizationRecommendation(
                    stage='pose_stage',
                    optimization_type=OptimizationType.ALGORITHM,
                    title="Use lightweight pose model variant",
                    description="Switch from BlazePose Full to BlazePose Lite for 2x speedup (trade-off: accuracy)",
                    expected_improvement_percent=50,
                    difficulty='easy',
                    priority='low',
                    implementation_time_hours=2,
                    risk_level='medium',
                ),
                OptimizationRecommendation(
                    stage='pose_stage',
                    optimization_type=OptimizationType.CACHING,
                    title="Cache pose model weights",
                    description="Pre-load and cache model to avoid repeated disk I/O",
                    expected_improvement_percent=10,
                    difficulty='easy',
                    priority='low',
                    implementation_time_hours=1,
                    risk_level='low',
                ),
            ],
            
            'torso_crop_stage': [
                OptimizationRecommendation(
                    stage='torso_crop_stage',
                    optimization_type=OptimizationType.BATCHING,
                    title="Batch crop operations",
                    description="Use vectorized numpy operations instead of per-frame processing",
                    expected_improvement_percent=35,
                    difficulty='medium',
                    priority='medium',
                    implementation_time_hours=3,
                    risk_level='low',
                ),
                OptimizationRecommendation(
                    stage='torso_crop_stage',
                    optimization_type=OptimizationType.IO,
                    title="Use memory-mapped arrays",
                    description="Store frames in shared memory for faster access from multiple processes",
                    expected_improvement_percent=20,
                    difficulty='hard',
                    priority='low',
                    implementation_time_hours=6,
                    risk_level='medium',
                ),
            ],
            
            'jersey_ocr_stage': [
                OptimizationRecommendation(
                    stage='jersey_ocr_stage',
                    optimization_type=OptimizationType.CACHING,
                    title="Cache OCR model weights",
                    description="Load OCR model once and reuse across stages",
                    expected_improvement_percent=40,
                    difficulty='easy',
                    priority='high',
                    implementation_time_hours=2,
                    risk_level='low',
                ),
                OptimizationRecommendation(
                    stage='jersey_ocr_stage',
                    optimization_type=OptimizationType.ALGORITHM,
                    title="Use lightweight OCR model",
                    description="Switch to PaddleOCR (smaller, faster) if accuracy sufficient",
                    expected_improvement_percent=50,
                    difficulty='medium',
                    priority='medium',
                    implementation_time_hours=4,
                    risk_level='medium',
                ),
            ],
            
            'rep_extraction_stage': [
                OptimizationRecommendation(
                    stage='rep_extraction_stage',
                    optimization_type=OptimizationType.ALGORITHM,
                    title="Implement fast trajectory analysis",
                    description="Use spatial hashing instead of O(n²) pairwise comparisons",
                    expected_improvement_percent=60,
                    difficulty='hard',
                    priority='medium',
                    implementation_time_hours=8,
                    risk_level='low',
                ),
            ],
            
            'biomechanics_stage': [
                OptimizationRecommendation(
                    stage='biomechanics_stage',
                    optimization_type=OptimizationType.BATCHING,
                    title="Batch trait scoring calculations",
                    description="Use NumPy vectorization instead of per-rep loops",
                    expected_improvement_percent=40,
                    difficulty='medium',
                    priority='medium',
                    implementation_time_hours=4,
                    risk_level='low',
                ),
                OptimizationRecommendation(
                    stage='biomechanics_stage',
                    optimization_type=OptimizationType.CACHING,
                    title="Cache trait calculation results",
                    description="Memoize expensive calculations for repeated patterns",
                    expected_improvement_percent=25,
                    difficulty='medium',
                    priority='low',
                    implementation_time_hours=3,
                    risk_level='low',
                ),
            ],
            
            'complete_stage': [
                OptimizationRecommendation(
                    stage='complete_stage',
                    optimization_type=OptimizationType.RESOURCE,
                    title="Parallelize completion tasks",
                    description="Run cleanup, logging, and notifications in parallel",
                    expected_improvement_percent=50,
                    difficulty='easy',
                    priority='low',
                    implementation_time_hours=2,
                    risk_level='low',
                ),
            ],
        }
        
        return recommendations.get(stage, [])
    
    def generate_optimization_plan(self, metrics: Dict) -> str:
        """Generate prioritized optimization plan."""
        analysis = self.analyze_metrics(metrics)
        
        # Collect all recommendations for stages with issues
        all_recommendations = []
        for stage_name, stage_analysis in analysis.items():
            if stage_analysis['status'] != 'normal':
                recommendations = self.get_recommendations_for_stage(stage_name)
                all_recommendations.extend(recommendations)
        
        # Sort by priority and expected improvement
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        all_recommendations.sort(
            key=lambda r: (
                priority_order.get(r.priority, 4),
                -r.expected_improvement_percent
            )
        )
        
        # Generate report
        lines = [
            "# Optimization Plan for TEAM-GRADE Pipeline",
            "",
            "## Priority Order",
            "",
        ]
        
        for i, rec in enumerate(all_recommendations, 1):
            lines.append(f"### {i}. {rec.title}")
            lines.extend([
                f"- **Stage:** {rec.stage}",
                f"- **Type:** {rec.optimization_type.value}",
                f"- **Priority:** {rec.priority}",
                f"- **Expected Improvement:** {rec.expected_improvement_percent:.0f}%",
                f"- **Difficulty:** {rec.difficulty}",
                f"- **Time Estimate:** {rec.implementation_time_hours:.1f} hours",
                f"- **Risk Level:** {rec.risk_level}",
                f"- **Description:** {rec.description}",
                "",
            ])
        
        return "\n".join(lines)


def generate_quick_wins(metrics: Dict) -> List[str]:
    """Identify quick-win optimizations (easy, high impact)."""
    analyzer = PerformanceAnalyzer()
    
    quick_wins = []
    for stage_name in metrics.keys():
        recommendations = analyzer.get_recommendations_for_stage(stage_name)
        for rec in recommendations:
            if rec.difficulty == 'easy' and rec.priority in ['high', 'critical']:
                quick_wins.append(
                    f"{rec.stage}: {rec.title} "
                    f"({rec.expected_improvement_percent:.0f}% improvement, "
                    f"{rec.implementation_time_hours:.1f}h)"
                )
    
    return sorted(quick_wins)
