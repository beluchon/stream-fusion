import re
from typing import List, Dict

from RTN import ParsedData, title_match
from stream_fusion.constants import FRENCH_PATTERNS
from stream_fusion.utils.filter.base_filter import BaseFilter
from stream_fusion.logging_config import logger
from stream_fusion.utils.torrent.torrent_item import TorrentItem


class LanguagePriorityFilter(BaseFilter):
    """
    Filtre pour trier les torrents selon une priorité de langue spécifique.
    Ordre de priorité:
    1. Groupe 1: VFF, VOF, VFI
    2. Groupe 2: VF2, VFQ
    3. Groupe 3: VOST
    """

    def __init__(self, config):
        super().__init__(config)
        # Définition des groupes de priorité pour les langues
        self.language_priority_groups = {
            # Groupe 1 (priorité la plus élevée)
            1: ["VFF", "VOF", "VFI"],
            # Groupe 2 (priorité moyenne)
            2: ["VF2", "VFQ"],
            # Groupe 3 (priorité basse)
            3: ["VOSTFR"],
            # Groupe 4 (priorité la plus basse - autres langues)
            4: ["VQ", "FRENCH"]
        }
        
        # Créer un dictionnaire inversé pour un accès rapide à la priorité par langue
        self.language_priority_map = {}
        for priority, languages in self.language_priority_groups.items():
            for lang in languages:
                self.language_priority_map[lang] = priority

    def filter(self, data: List[TorrentItem]) -> List[TorrentItem]:
        """
        Trie les torrents selon la priorité de langue définie.
        Utilise RTN pour l'analyse et le classement des torrents.
        """
        for torrent in data:
            language_priority = self._get_language_priority(torrent)
            
            torrent.language_priority = language_priority
            
            logger.trace(f"Torrent {torrent.raw_title} a une priorité de langue: {language_priority}")


        sorted_data = sorted(data, key=lambda x: x.language_priority)
        
        logger.info(f"Tri par langue terminé. Ordre des langues: VFF/VOF/VFI > VF2/VFQ > VOST > autres")
        
        return sorted_data

    def _get_language_priority(self, torrent: TorrentItem) -> int:
        """
        Détermine la priorité de langue d'un torrent.
        
        Args:
            torrent: L'objet torrent à évaluer
            
        Returns:
            int: Valeur de priorité (plus petit = plus prioritaire)
        """
        language = self._detect_language_from_title(torrent.raw_title)
        
        if not language:
            if hasattr(torrent, 'languages') and torrent.languages:
                best_priority = 999
                for lang in torrent.languages:
                    lang_code = self._convert_language_code(lang)
                    if lang_code in self.language_priority_map:
                        priority = self.language_priority_map[lang_code]
                        best_priority = min(best_priority, priority)
                return best_priority
            return 999  
        
        return self.language_priority_map.get(language, 998)  
    
    def _detect_language_from_title(self, title: str) -> str:
        """
        Détecte la langue à partir du titre du torrent.
        
        Args:
            title: Titre du torrent
            
        Returns:
            str: Code de langue détecté ou None
        """
        if not title:
            return None
            
        for language, pattern in FRENCH_PATTERNS.items():
            if re.search(pattern, title, re.IGNORECASE):
                return language
        
        return None
        
    def _convert_language_code(self, lang_code: str) -> str:
        """
        Convertit les codes de langue courts en codes correspondant à nos groupes de priorité.
        
        Args:
            lang_code: Code de langue court (ex: 'fr', 'multi')
            
        Returns:
            str: Code de langue correspondant à nos groupes ou None
        """
        lang_mapping = {
            'fr': 'FRENCH',
            'vff': 'VFF',
            'vf': 'FRENCH',
            'vostfr': 'VOSTFR',
            'multi': 'VFF', 
            'voi': 'VOF',
            'vfi': 'VFI',
            'vf2': 'VF2',
            'vfq': 'VFQ'
        }
        
        return lang_mapping.get(lang_code.lower(), None)

    def can_filter(self):
        """
        Ce filtre peut toujours être appliqué, car il s'agit d'un tri et non d'une exclusion.
        """
        return True
        
