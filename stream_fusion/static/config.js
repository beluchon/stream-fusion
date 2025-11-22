const sorts = ['quality', 'sizedesc', 'sizeasc', 'qualitythensize'];
const qualityExclusions = ['2160p', '1080p', '720p', '480p', 'rips', 'cam', 'hevc', 'unknown'];
const languages = ['en', 'fr', 'multi', 'vfq'];

// Débrideurs implémentés nativement dans Stream Fusion
const implementedDebrids = ['debrid_rd', 'debrid_ad', 'debrid_tb', 'debrid_pm', 'sharewood', 'yggflix'];

// Débrideurs qui nécessitent StremThru pour fonctionner
const unimplementedDebrids = ['debrid_dl', 'debrid_ed', 'debrid_oc', 'debrid_pk'];

document.addEventListener('DOMContentLoaded', function () {
    loadData();
    handleUniqueAccounts();
    updateProviderFields();
    updateDebridOrderList();
    toggleStremThruFields();
});

// ... [Le reste des fonctions jusqu'à loadData reste inchangé] ...

function loadData() {
    const currentUrl = window.location.href;
    let data = currentUrl.match(/\/([^\/]+)\/configure$/);
    let decodedData = {};
    if (data && data[1]) {
        try {
            decodedData = JSON.parse(atob(data[1]));
        } catch (error) {
            console.warn("No valid data to decode in URL, using default values.");
        }
    }

    function setElementValue(id, value, defaultValue) {
        const element = document.getElementById(id);
        if (element) {
            if (element.type === 'radio' || element.type === 'checkbox') {
                element.checked = (value !== undefined) ? value : defaultValue;
            } else {
                element.value = value || defaultValue || '';
            }
        }
    }

    const defaultConfig = {
        cache: true,
        cacheUrl: 'https://stremio-jackett-cacher.elfhosted.com/',
        zilean: true,
        yggflix: true,
        sharewood: false,
        maxSize: '18',
        resultsPerQuality: '10',
        maxResults: '30',
        minCachedResults: '10',
        torrenting: false,
        ctg_yggtorrent: true,
        ctg_yggflix: false,
        metadataProvider: 'tmdb',
        sort: 'qualitythensize',
        exclusion: ['cam', '2160p'],
        languages: ['fr', 'multi'],
        debrid_rd: false,
        debrid_ad: false,
        debrid_tb: false,
        debrid_pm: false,
        tb_usenet: false,
        tb_search: false,
        debrid_order: false
    };

    Object.keys(defaultConfig).forEach(key => {
        const value = decodedData[key] !== undefined ? decodedData[key] : defaultConfig[key];
        if (key === 'metadataProvider') {
            setElementValue('tmdb', value === 'tmdb', true);
            setElementValue('cinemeta', value === 'cinemeta', false);
        } else if (key === 'sort') {
            sorts.forEach(sort => {
                setElementValue(sort, value === sort, sort === defaultConfig.sort);
            });
        } else if (key === 'exclusion') {
            qualityExclusions.forEach(quality => {
                setElementValue(quality, value.includes(quality), defaultConfig.exclusion.includes(quality));
            });
        } else if (key === 'languages') {
            languages.forEach(language => {
                setElementValue(language, value.includes(language), defaultConfig.languages.includes(language));
            });
        } else {
            setElementValue(key, value, defaultConfig[key]);
        }
    });

    const serviceArray = decodedData.service || [];
    setElementValue('debrid_rd', serviceArray.includes('Real-Debrid'), defaultConfig.debrid_rd);
    setElementValue('debrid_ad', serviceArray.includes('AllDebrid'), defaultConfig.debrid_ad);
    setElementValue('debrid_tb', serviceArray.includes('TorBox'), defaultConfig.debrid_tb);
    setElementValue('debrid_pm', serviceArray.includes('Premiumize'), defaultConfig.debrid_pm);
    setElementValue('debrid_order', serviceArray.length > 0, defaultConfig.debrid_order);
    
    setElementValue('ctg_yggtorrent', decodedData.yggtorrentCtg, defaultConfig.ctg_yggtorrent);
    setElementValue('ctg_yggflix', decodedData.yggflixCtg, defaultConfig.ctg_yggflix);
    
    setElementValue('rd_token_info', decodedData.RDToken, '');
    setElementValue('ad_token_info', decodedData.ADToken, '');
    setElementValue('tb_token_info', decodedData.TBToken, '');
    setElementValue('pm_token_info', decodedData.PMToken, '');
    setElementValue('sharewoodPasskey', decodedData.sharewoodPasskey, '');
    setElementValue('yggPasskey', decodedData.yggPasskey, '');
    setElementValue('ApiKey', decodedData.apiKey, '');
    setElementValue('exclusion-keywords', (decodedData.exclusionKeywords || []).join(', '), '');
    
    setElementValue('tb_usenet', decodedData.TBUsenet, defaultConfig.tb_usenet);
    setElementValue('tb_search', decodedData.TBSearch, defaultConfig.tb_search);

    handleUniqueAccounts();
    updateProviderFields();

    const debridDownloader = decodedData.debridDownloader;
    if (debridDownloader) {
        const radioButton = document.querySelector(`input[name="debrid_downloader"][value="${debridDownloader}"]`);
        if (radioButton) {
            radioButton.checked = true;
        }
    }

    updateDebridDownloaderOptions();
    updateDebridOrderList();
    ensureDebridConsistency();
}

function getLink(method) {
    const data = {
        addonHost: new URL(window.location.href).origin,
        apiKey: document.getElementById('ApiKey').value,
        service: [],
        RDToken: document.getElementById('rd_token_info')?.value,
        ADToken: document.getElementById('ad_token_info')?.value,
        TBToken: document.getElementById('tb_token_info')?.value,
        PMToken: document.getElementById('pm_token_info')?.value,
        TBUsenet: document.getElementById('tb_usenet')?.checked,
        TBSearch: document.getElementById('tb_search')?.checked,
        sharewoodPasskey: document.getElementById('sharewoodPasskey')?.value,
        maxSize: parseInt(document.getElementById('maxSize').value) || 16,
        exclusionKeywords: document.getElementById('exclusion-keywords').value.split(',').map(keyword => keyword.trim()).filter(keyword => keyword !== ''),
        languages: languages.filter(lang => document.getElementById(lang).checked),
        sort: sorts.find(sort => document.getElementById(sort).checked),
        resultsPerQuality: parseInt(document.getElementById('resultsPerQuality').value) || 5,
        maxResults: parseInt(document.getElementById('maxResults').value) || 5,
        minCachedResults: parseInt(document.getElementById('minCachedResults').value) || 5,
        exclusion: qualityExclusions.filter(quality => document.getElementById(quality).checked),
        cacheUrl: document.getElementById('cacheUrl')?.value,
        cache: document.getElementById('cache')?.checked,
        zilean: document.getElementById('zilean')?.checked,
        yggflix: document.getElementById('yggflix')?.checked,
        sharewood: document.getElementById('sharewood')?.checked,
        yggtorrentCtg: document.getElementById('ctg_yggtorrent')?.checked,
        yggflixCtg: document.getElementById('ctg_yggflix')?.checked,
        yggPasskey: document.getElementById('yggPasskey')?.value,
        torrenting: document.getElementById('torrenting').checked,
        debrid: false,
        metadataProvider: document.getElementById('tmdb').checked ? 'tmdb' : 'cinemeta',
        debridDownloader: document.querySelector('input[name="debrid_downloader"]:checked')?.value,
        // StremThru configuration
        stremthru: document.getElementById('stremthru_enabled')?.checked || false,
        stremthruUrl: document.getElementById('stremthru_url')?.value || 'https://stremthru.13377001.xyz',
        // Nouveaux débrideurs
        debridlinkApiKey: document.getElementById('debridlink_api_key')?.value || '',
        easydebridApiKey: document.getElementById('easydebrid_api_key')?.value || '',
        offcloudCredentials: document.getElementById('offcloud_credentials')?.value || '',
        pikpakCredentials: document.getElementById('pikpak_credentials')?.value || ''
    };

    data.service = Array.from(document.getElementById('debridOrderList').children).map(li => li.dataset.serviceName);
    data.debrid = data.service.length > 0;

    // ... [Le reste de la fonction getLink reste inchangé] ...
    
    const missingRequiredFields = [];

    if (data.cache && !data.cacheUrl) missingRequiredFields.push("Cache URL");
    if (data.service.includes('Real-Debrid') && document.getElementById('rd_token_info') && !data.RDToken) missingRequiredFields.push("Real-Debrid Account Connection");
    // ... [Suite des vérifications] ...

    if (missingRequiredFields.length > 0) {
        alert(`Please fill all required fields: ${missingRequiredFields.join(", ")}`);
        return false;
    }
    
    // ... [Reste de la fonction] ...
    
    function validatePasskey(passkey) {
        return /^[a-zA-Z0-9]{32}$/.test(passkey);
    }

    function validateApiKey(apiKey) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(apiKey);
    }

    if (data.yggflix && data.yggPasskey && !validatePasskey(data.yggPasskey)) {
        alert('Ygg Passkey doit contenir exactement 32 caractères alphanumériques');
        return false;
    }

    if (data.sharewood && data.sharewoodPasskey && !validatePasskey(data.sharewoodPasskey)) {
        alert('Sharewood Passkey doit contenir exactement 32 caractères alphanumériques');
        return false;
    }

    if (data.apiKey && !validateApiKey(data.apiKey)) {
        alert('APIKEY doit être un UUID v4 valide');
        return false;
    }

    const encodedData = btoa(JSON.stringify(data));
    const stremio_link = `${window.location.host}/${encodedData}/manifest.json`;

    if (method === 'link') {
        window.open(`stremio://${stremio_link}`, "_blank");
    } else if (method === 'copy') {
        const link = window.location.protocol + '//' + stremio_link;
        navigator.clipboard.writeText(link).then(() => {
            alert('Link copied to clipboard');
        }, () => {
            alert('Error copying link to clipboard');
        });
    }
}

let showLanguageCheckBoxes = true;
function showCheckboxes() {
    let checkboxes = document.getElementById("languageCheckBoxes");
    checkboxes.style.display = showLanguageCheckBoxes ? "block" : "none";
    showLanguageCheckBoxes = !showLanguageCheckBoxes;
}
