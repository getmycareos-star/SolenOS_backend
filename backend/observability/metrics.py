import time
from typing import Dict, Optional
from collections import defaultdict
from threading import Lock
from observability.logging import logger

class MetricsCollector:
    """Collect and store application metrics"""
    
    def __init__(self):
        self._lock = Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, list] = defaultdict(list)
        self._start_time = time.time()
    
    def increment_counter(self, name: str, value: int = 1, tags: Optional[Dict[str, str]] = None):
        """Increment a counter metric"""
        with self._lock:
            key = self._make_key(name, tags)
            self._counters[key] += value
            logger.debug(f"Incremented counter {key} by {value}", 
                        extra={"metric_name": name, "metric_type": "counter", "value": value})
    
    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Set a gauge metric"""
        with self._lock:
            key = self._make_key(name, tags)
            self._gauges[key] = value
            logger.debug(f"Set gauge {key} to {value}", 
                        extra={"metric_name": name, "metric_type": "gauge", "value": value})
    
    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """Record a value in a histogram"""
        with self._lock:
            key = self._make_key(name, tags)
            self._histograms[key].append(value)
            # Keep only last 1000 values to prevent memory growth
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-1000:]
            logger.debug(f"Recorded histogram {key} value {value}", 
                        extra={"metric_name": name, "metric_type": "histogram", "value": value})
    
    def get_counter(self, name: str, tags: Optional[Dict[str, str]] = None) -> int:
        """Get counter value"""
        with self._lock:
            key = self._make_key(name, tags)
            return self._counters.get(key, 0)
    
    def get_gauge(self, name: str, tags: Optional[Dict[str, str]] = None) -> float:
        """Get gauge value"""
        with self._lock:
            key = self._make_key(name, tags)
            return self._gauges.get(key, 0.0)
    
    def get_histogram_stats(self, name: str, tags: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """Get histogram statistics (count, mean, min, max, p50, p95, p99)"""
        with self._lock:
            key = self._make_key(name, tags)
            values = self._histograms.get(key, [])
            if not values:
                return {"count": 0}
            
            sorted_values = sorted(values)
            count = len(sorted_values)
            return {
                "count": count,
                "mean": sum(sorted_values) / count,
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "p50": sorted_values[int(count * 0.5)],
                "p95": sorted_values[int(count * 0.95)],
                "p99": sorted_values[int(count * 0.99)]
            }
    
    def get_all_metrics(self) -> Dict[str, Dict]:
        """Get all metrics"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: self.get_histogram_stats(k.split(':')[0], 
                                              self._parse_tags(':'.join(k.split(':')[1:])) if ':' in k else None)
                    for k in self._histograms.keys()
                },
                "uptime_seconds": time.time() - self._start_time
            }
    
    def _make_key(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """Create a metric key from name and tags"""
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}:{tag_str}"
    
    def _parse_tags(self, tag_str: str) -> Dict[str, str]:
        """Parse tags from a tag string"""
        tags = {}
        if tag_str:
            for pair in tag_str.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    tags[k] = v
        return tags

# Global metrics collector instance
metrics = MetricsCollector()

# Convenience functions
def increment_counter(name: str, value: int = 1, tags: Optional[Dict[str, str]] = None):
    metrics.increment_counter(name, value, tags)

def set_gauge(name: str, value: float, tags: Optional[Dict[str, str]] = None):
    metrics.set_gauge(name, value, tags)

def record_histogram(name: str, value: float, tags: Optional[Dict[str, str]] = None):
    metrics.record_histogram(name, value, tags)