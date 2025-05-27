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
        # Ajouter l'information de priorité de langue à chaque torrent
        for torrent in data:
            # Déterminer la priorité de langue
            language_priority = self._get_language_priority(torrent)
            
            # Ajouter la priorité comme attribut au torrent pour le tri
            torrent.language_priority = language_priority
            
            logger.trace(f"Torrent {torrent.raw_title} a une priorité de langue: {language_priority}")

        # Tri uniquement par priorité de langue (plus petit = plus prioritaire)
        # Nous ne faisons pas de tri secondaire par qualité pour éviter les doublons avec d'autres filtres
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
        # Détecter la langue à partir du titre
        language = self._detect_language_from_title(torrent.raw_title)
        
        if not language:
            # Si aucune langue n'est détectée dans le titre, vérifier les langues du torrent
            if hasattr(torrent, 'languages') and torrent.languages:
                # Parcourir les langues du torrent et trouver celle avec la priorité la plus élevée
                best_priority = 999
                for lang in torrent.languages:
                    # Convertir les codes de langue courts en codes correspondant à nos groupes
                    lang_code = self._convert_language_code(lang)
                    if lang_code in self.language_priority_map:
                        priority = self.language_priority_map[lang_code]
                        best_priority = min(best_priority, priority)
                return best_priority
            return 999  # Priorité la plus basse pour les langues non détectées
        
        # Utiliser le dictionnaire de mappage pour un accès direct à la priorité
        return self.language_priority_map.get(language, 998)  # 998 pour les langues connues mais non classées
    
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
        # Mapping des codes de langue courts vers nos codes de priorité
        lang_mapping = {
            'fr': 'FRENCH',
            'vff': 'VFF',
            'vf': 'FRENCH',
            'vostfr': 'VOSTFR',
            'multi': 'VFF',  # Considérer multi comme VFF (haute priorité)
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
        
    # Nous avons supprimé la méthode _get_quality_score pour éviter les doublons avec d'autres filtres de qualité
