from stream_fusion.utils.filter.base_filter import BaseFilter
from stream_fusion.logging_config import logger

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
        logger.info(f"ResultsPerQualityFilter: Using sort method: {sort_method} (RTN will handle sorting)")
        
        if sort_method in ['sizedesc', 'sizeasc', 'qualitythensize']:
            logger.info(f"ResultsPerQualityFilter: Size-based sorting detected, passing all items to RTN")
            for resolution, items in resolution_groups.items():
                filtered_items.extend(items)
                logger.debug(f"ResultsPerQualityFilter: Passing all {len(items)} items for resolution {resolution} to RTN")
        else:
            for resolution, items in resolution_groups.items():


                limited_items = items[:self.max_results_per_quality]
                filtered_items.extend(limited_items)
                
                if limited_items and len(limited_items) > 0:
                    sizes_gb = [int(item.size) / (1024*1024*1024) for item in limited_items]
                    logger.info(f"ResultsPerQualityFilter: For {resolution}, selected file sizes (GB): {', '.join([f'{size:.2f}' for size in sizes_gb])}")
                
                logger.debug(f"ResultsPerQualityFilter: Kept {len(limited_items)} items for resolution {resolution}")
        
        

        logger.debug(f"ResultsPerQualityFilter: input {len(data)}, output {len(filtered_items)}")
        return filtered_items

    def can_filter(self):
        can_apply = self.max_results_per_quality > 0
        logger.debug(f"ResultsPerQualityFilter.can_filter() returned {can_apply} with max_results_per_quality={self.max_results_per_quality}")
        return can_apply
