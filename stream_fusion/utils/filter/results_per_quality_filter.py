from stream_fusion.utils.filter.base_filter import BaseFilter
from stream_fusion.logging_config import logger

#TODO: Check if this filter is still needed and RTN changes for it.
class ResultsPerQualityFilter(BaseFilter):
    def __init__(self, config):
        super().__init__(config)
        self.max_results_per_quality = int(self.config.get('resultsPerQuality', 5))

    def filter(self, data):
        filtered_items = []
        resolution_groups = {}

        for item in data:
            resolution = getattr(item.parsed_data, 'resolution', "?.BZH.?")
            if resolution not in resolution_groups:
                resolution_groups[resolution] = []
            resolution_groups[resolution].append(item)
        
        sort_method = self.config.get('sort', '')
        for resolution, items in resolution_groups.items():
            if sort_method == 'sizedesc':
                sorted_items = sorted(items, key=lambda x: int(x.size), reverse=True)
                logger.debug(f"ResultsPerQualityFilter: Sorting {len(items)} items for resolution {resolution} by size descending")
            elif sort_method == 'sizeasc':
                sorted_items = sorted(items, key=lambda x: int(x.size))
                logger.debug(f"ResultsPerQualityFilter: Sorting {len(items)} items for resolution {resolution} by size ascending")
            elif sort_method == 'quality':
                sorted_items = sorted(items, key=lambda x: x.seeders if x.seeders is not None else 0, reverse=True)
                logger.debug(f"ResultsPerQualityFilter: Sorting {len(items)} items for resolution {resolution} by seeds (quality option)")
            elif sort_method == 'qualitythensize':
                sorted_items = sorted(items, key=lambda x: int(x.size), reverse=True)
                logger.debug(f"ResultsPerQualityFilter: Sorting {len(items)} items for resolution {resolution} by size descending (quality then size option)")
            else:
                sorted_items = sorted(items, key=lambda x: x.seeders if x.seeders is not None else 0, reverse=True)
                logger.debug(f"ResultsPerQualityFilter: Sorting {len(items)} items for resolution {resolution} by seeds (default)")
            
            filtered_items.extend(sorted_items[:self.max_results_per_quality])
            
            if sorted_items and len(sorted_items) > 0:
                sizes_gb = [int(item.size) / (1024*1024*1024) for item in sorted_items[:self.max_results_per_quality]]
                logger.info(f"ResultsPerQualityFilter: For {resolution}, selected file sizes (GB): {', '.join([f'{size:.2f}' for size in sizes_gb])}")
            
            logger.debug(f"ResultsPerQualityFilter: Kept {min(len(sorted_items), self.max_results_per_quality)} items for resolution {resolution}")

        logger.debug(f"ResultsPerQualityFilter: input {len(data)}, output {len(filtered_items)}")
        return filtered_items

    def can_filter(self):
        can_apply = self.max_results_per_quality > 0
        logger.debug(f"ResultsPerQualityFilter.can_filter() returned {can_apply} with max_results_per_quality={self.max_results_per_quality}")
        return can_apply
