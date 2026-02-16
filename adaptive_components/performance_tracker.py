# FILE: adaptive_components/performance_tracker.py
"""
Performance Tracker - Records all generation attempts for learning
Complete, production-ready implementation
"""
import json
import time
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class GenerationRecord:
    """Single generation attempt record"""
    timestamp: float
    module_name: str
    property_count: int
    chunk_size: int
    timeout: float
    prompt_variant: str
    success: bool
    quality_score: float
    generation_time: float
    error_type: Optional[str]
    error_message: Optional[str]
    llm_model: str
    
    def to_dict(self):
        return asdict(self)


class PerformanceTracker:
    """
    Tracks all generation attempts and provides learning data
    """
    def __init__(self, db_path: str = "performance_history.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for persistent storage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS generation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                module_name TEXT,
                property_count INTEGER,
                chunk_size INTEGER,
                timeout REAL,
                prompt_variant TEXT,
                success BOOLEAN,
                quality_score REAL,
                generation_time REAL,
                error_type TEXT,
                error_message TEXT,
                llm_model TEXT
            )
        ''')
        
        # Create indices for fast queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_property_count 
            ON generation_history(property_count)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_success 
            ON generation_history(success)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_chunk_size 
            ON generation_history(chunk_size)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp 
            ON generation_history(timestamp)
        ''')
        
        conn.commit()
        conn.close()
        
        print(f"✓ Performance tracker database initialized: {self.db_path}")
    
    def record_generation(self, record: GenerationRecord):
        """Store a generation attempt"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO generation_history 
            (timestamp, module_name, property_count, chunk_size, timeout, 
             prompt_variant, success, quality_score, generation_time, 
             error_type, error_message, llm_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record.timestamp,
            record.module_name,
            record.property_count,
            record.chunk_size,
            record.timeout,
            record.prompt_variant,
            record.success,
            record.quality_score,
            record.generation_time,
            record.error_type,
            record.error_message,
            record.llm_model
        ))
        
        conn.commit()
        conn.close()
    
    def get_similar_generations(
        self, 
        property_count: int, 
        tolerance: int = 10,
        limit: int = 50
    ) -> List[Dict]:
        """
        Get past generations with similar property counts
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM generation_history
            WHERE property_count BETWEEN ? AND ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (property_count - tolerance, property_count + tolerance, limit))
        
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_chunk_size_performance(self) -> Dict[int, Dict]:
        """
        Analyze performance by chunk size
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                chunk_size,
                COUNT(*) as total_attempts,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                AVG(quality_score) as avg_quality,
                AVG(generation_time) as avg_time
            FROM generation_history
            GROUP BY chunk_size
        ''')
        
        results = {}
        for row in cursor.fetchall():
            chunk_size, total, successes, avg_quality, avg_time = row
            results[chunk_size] = {
                'total_attempts': total,
                'successes': successes,
                'success_rate': successes / total if total > 0 else 0,
                'avg_quality': avg_quality or 0,
                'avg_time': avg_time or 0
            }
        
        conn.close()
        return results
    
    def get_prompt_variant_performance(self) -> Dict[str, Dict]:
        """
        Analyze performance by prompt variant
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                prompt_variant,
                COUNT(*) as total_attempts,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                AVG(quality_score) as avg_quality
            FROM generation_history
            GROUP BY prompt_variant
        ''')
        
        results = {}
        for row in cursor.fetchall():
            variant, total, successes, avg_quality = row
            results[variant] = {
                'total_attempts': total,
                'successes': successes,
                'success_rate': successes / total if total > 0 else 0,
                'avg_quality': avg_quality or 0
            }
        
        conn.close()
        return results
    
    def get_failure_patterns(self, limit: int = 100) -> List[Dict]:
        """
        Get recent failures for analysis
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM generation_history
            WHERE success = 0
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_statistics(self) -> Dict:
        """
        Overall statistics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                AVG(quality_score) as avg_quality,
                AVG(generation_time) as avg_time
            FROM generation_history
        ''')
        
        row = cursor.fetchone()
        total, successes, avg_quality, avg_time = row
        
        conn.close()
        
        return {
            'total_generations': total or 0,
            'total_successes': successes or 0,
            'overall_success_rate': (successes / total) if total > 0 else 0,
            'avg_quality': avg_quality or 0,
            'avg_generation_time': avg_time or 0
        }
    
    def get_learning_curve(self, window_size: int = 20) -> List[Dict]:
        """
        Get success rate over time in windows
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                id,
                success,
                quality_score,
                generation_time
            FROM generation_history
            ORDER BY timestamp ASC
        ''')
        
        all_records = cursor.fetchall()
        conn.close()
        
        learning_curve = []
        for i in range(0, len(all_records), window_size):
            window = all_records[i:i+window_size]
            if window:
                successes = sum(1 for r in window if r[1])
                avg_quality = sum(r[2] for r in window) / len(window)
                avg_time = sum(r[3] for r in window) / len(window)
                
                learning_curve.append({
                    'window_start': i,
                    'window_end': i + len(window),
                    'success_rate': successes / len(window),
                    'avg_quality': avg_quality,
                    'avg_time': avg_time
                })
        
        return learning_curve
    
    def export_to_json(self, output_path: str):
        """Export all data for analysis"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM generation_history ORDER BY timestamp ASC')
        columns = [desc[0] for desc in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        conn.close()
        
        # Add analysis
        export_data = {
            'metadata': {
                'export_time': datetime.now().isoformat(),
                'total_records': len(results),
                'statistics': self.get_statistics()
            },
            'records': results,
            'chunk_size_analysis': self.get_chunk_size_performance(),
            'prompt_variant_analysis': self.get_prompt_variant_performance(),
            'learning_curve': self.get_learning_curve()
        }
        
        with open(output_path, 'w') as f:
            json.dump(export_data, f, indent=2)
        
        print(f"✓ Exported {len(results)} records to {output_path}")
    
    def clear_history(self):
        """Clear all history (use with caution!)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM generation_history')
        conn.commit()
        conn.close()
        print("⚠ All history cleared")


# Test code
if __name__ == "__main__":
    print("Testing Performance Tracker...\n")
    
    tracker = PerformanceTracker("test_performance.db")
    
    # Add test records
    for i in range(10):
        record = GenerationRecord(
            timestamp=time.time(),
            module_name=f"TestModule_{i}",
            property_count=50,
            chunk_size=20,
            timeout=60.0,
            prompt_variant="detailed",
            success=(i % 3 != 0),  # 2/3 success rate
            quality_score=0.8 if (i % 3 != 0) else 0.3,
            generation_time=45.0 + i,
            error_type=None if (i % 3 != 0) else "TimeoutError",
            error_message=None if (i % 3 != 0) else "Generation timed out",
            llm_model="qwen2.5-coder:7b"
        )
        tracker.record_generation(record)
        time.sleep(0.1)
    
    print("\n✓ 10 test records added")
    
    # Get statistics
    stats = tracker.get_statistics()
    print(f"\nStatistics:")
    print(f"  Total generations: {stats['total_generations']}")
    print(f"  Success rate: {stats['overall_success_rate']:.1%}")
    print(f"  Avg quality: {stats['avg_quality']:.2f}")
    
    # Export
    tracker.export_to_json("test_export.json")
    
    print("\n✓ Performance Tracker test complete!")