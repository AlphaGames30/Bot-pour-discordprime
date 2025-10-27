#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Application principale qui lance le bot Discord et le serveur web de statut
Système de heartbeat robuste pour maintenir l'activité sur Render
"""

import threading
import time
import os
import asyncio
import logging
from datetime import datetime
import signal
import sys
import random

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Variables globales pour le contrôle
shutdown_event = threading.Event()
fatal_error_state = None
app_start_time = datetime.now()
current_bot_instance = None
bot_status_lock = threading.Lock()
fatal_error_lock = threading.Lock()  # Lock pour les erreurs fatales
bot_status_data_lock = threading.Lock()  # Lock pour bot_status data
last_fatal_error_time = None

def signal_handler(signum, frame):
    """Gestionnaire de signal pour arrêt propre"""
    logger.info("🔴 Signal d'arrêt reçu, fermeture en cours...")
    shutdown_event.set()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def run_discord_bot():
    """Lancer le bot Discord dans un thread séparé avec gestion des reconnexions"""
    global fatal_error_state, current_bot_instance, last_fatal_error_time
    retry_count = 0
    base_delay = 2
    max_delay = 300  # 5 minutes max
    
    # Importer le bot une seule fois - gérer les erreurs dans la boucle principale
    bot_module = None
    BotConfigurationError = Exception  # Fallback en cas d'import error
    discord_module = None
    
    try:
        import bot as bot_module
        from bot import BotConfigurationError
        import discord as discord_module
    except ImportError as e:
        logger.error(f"❌ Erreur d'import du bot: {e}")
        with fatal_error_lock:
            fatal_error_state = f"Import error: {e}"
            last_fatal_error_time = datetime.now()
        logger.info("🔄 Le thread Discord continue pour permettre la récupération automatique...")
    
    # Vérifier la configuration - gérer l'erreur dans la boucle principale
    if bot_module and not bot_module.check_token():
        logger.error("❌ Token Discord manquant")
        with fatal_error_lock:
            fatal_error_state = "Token Discord manquant"
            last_fatal_error_time = datetime.now()
        logger.info("🔄 Le thread Discord continue pour permettre la récupération automatique...")
    
    while not shutdown_event.is_set():
        try:
            # Vérifier si on peut tenter un démarrage (pas d'erreur fatale ou récupération possible)
            with fatal_error_lock:
                current_fatal_state = fatal_error_state
                error_time = last_fatal_error_time
                
                # Tenter la récupération si l'erreur est ancienne
                if current_fatal_state and error_time:
                    time_since_error = (datetime.now() - error_time).total_seconds()
                    if time_since_error > 600:  # 10 minutes
                        logger.info(f"🔄 Tentative de récupération après {time_since_error:.0f}s: {current_fatal_state}")
                        # Tenter de réimporter et reconfigurer
                        try:
                            logger.info("🔍 Test de récupération avec validation complète...")
                            # Étape 1: Réimporter si nécessaire
                            if "Import error" in current_fatal_state:
                                import bot as bot_module
                                from bot import BotConfigurationError as LocalBotConfigurationError
                                import discord as discord_module
                                logger.info("✅ Ré-import du bot réussi")
                            
                            # Étape 2: Validation complète avec preflight check
                            if bot_module and discord_module:
                                if not bot_module.check_token():
                                    logger.info("❌ Token toujours manquant")
                                else:
                                    # Étape 3: Test de création et connexion (preflight)
                                    try:
                                        test_bot = bot_module.create_bot()
                                        logger.info("✅ Création du bot réussie")
                                        
                                        # Test de login rapide sans run complet
                                        import asyncio
                                        async def test_login():
                                            await test_bot.login(bot_module.TOKEN)
                                            await test_bot.close()
                                        
                                        # Exécuter le test de login
                                        asyncio.run(test_login())
                                        logger.info("✅ Test de connexion Discord réussi")
                                        
                                        # Si on arrive ici, tout est bon - clear l'erreur fatale
                                        fatal_error_state = None
                                        last_fatal_error_time = None
                                        retry_count = 0
                                        logger.info("✨ Récupération automatique réussie - tous les tests passés!")
                                        
                                    except Exception as preflight_error:
                                        logger.warning(f"❌ Test de connexion échoué: {preflight_error}")
                            else:
                                logger.info("❌ Modules toujours non disponibles")
                                
                        except Exception as recovery_error:
                            logger.warning(f"⚠️ Échec de la récupération: {recovery_error}")
                            
                current_fatal_state = fatal_error_state
                
            # Si on a encore une erreur fatale, attendre avant de réessayer
            if current_fatal_state:
                logger.info(f"⏳ Attente (erreur fatale active): {current_fatal_state}")
                for _ in range(30):  # Attendre 30 secondes
                    if shutdown_event.is_set():
                        break
                    time.sleep(1)
                continue
                
            # Essayer de démarrer le bot si tout est OK
            if not bot_module or not discord_module:
                raise Exception("Modules bot ou discord non disponibles")
                
            logger.info(f"🤖 Démarrage du bot Discord (tentative {retry_count + 1})...")
            
            # Créer une nouvelle instance du bot pour chaque tentative
            with bot_status_lock:
                current_bot_instance = bot_module.create_bot()
            
            # Lancer le bot
            current_bot_instance.run(bot_module.TOKEN, reconnect=True)
            
        except BotConfigurationError as e:
            logger.error(f"❌ Erreur de configuration fatale: {e}")
            with fatal_error_lock:
                fatal_error_state = f"Configuration error: {e}"
                last_fatal_error_time = datetime.now()
            # Ne pas break - continuer la boucle pour permettre la récupération automatique
            logger.info("🔄 Attente de la récupération automatique (10 minutes)...")
            
        except Exception as e:
            # Capturer les erreurs Discord et autres (y compris LoginFailure)
            error_type = type(e).__name__
            if error_type == 'LoginFailure':
                logger.error(f"❌ Token Discord invalide: {e}")
                with fatal_error_lock:
                    fatal_error_state = f"Invalid token: {e}"
                    last_fatal_error_time = datetime.now()
                logger.info("🔄 Attente de la récupération automatique (10 minutes)...")
            else:
                retry_count += 1
                error_type = type(e).__name__
                
            logger.error(f"❌ Erreur lors du démarrage du bot Discord ({error_type}): {e}")
            
            if shutdown_event.is_set():
                break
                
            # Backoff exponentiel avec jitter
            wait_time = min(base_delay * (2 ** min(retry_count, 8)), max_delay)
            jitter = random.uniform(0.8, 1.2)  # Jitter de ±20%
            wait_time = wait_time * jitter
            
            logger.info(f"⏳ Nouvelle tentative dans {wait_time:.1f} secondes...")
            
            # Ne pas clear l'erreur fatale ici - laisse la logique principale s'en occuper
            # La récupération validée se fait au début de la boucle principale
                        
            # Attendre avec vérification d'arrêt
            for _ in range(int(wait_time)):
                if shutdown_event.is_set():
                    break
                time.sleep(1)
        finally:
            # Nettoyer la référence du bot
            with bot_status_lock:
                current_bot_instance = None
                
    logger.info("🔴 Thread Discord arrêté")


def run_web_server():
    """Lancer le serveur web Flask avec endpoints de santé"""
    try:
        from flask import Flask, render_template_string, jsonify
        # Ne pas importer bot ici - utiliser les références globales pour éviter les échecs de Flask
        
        app = Flask(__name__)
        
        # Configuration Flask pour éviter le cache
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        
# Variables globales pour le statut
        bot_status = {
            'online': False,
            'first_online_time': None,
            'offline_since': None,
            'bot_name': 'Bot Discord',
            'servers': 0,
            'last_update': None,
            'reconnect_count': 0
        }

        # Template HTML amélioré avec design responsive
        HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Statut du Bot Discord - Heartbeat Actif</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            margin: 0;
            padding: 20px;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        
        .container {
            background: white;
            border-radius: 15px;
            padding: 40px;
            box-shadow: 0 15px 35px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 600px;
            width: 100%;
        }
        
        .status-indicator {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 10px;
            animation: pulse 2s infinite;
        }
        
        .online {
            background-color: #4CAF50;
            box-shadow: 0 0 0 0 rgba(76, 175, 80, 1);
        }
        
        .offline {
            background-color: #f44336;
            box-shadow: 0 0 0 0 rgba(244, 67, 54, 1);
        }
        
        @keyframes pulse {
            0% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.7);
            }
            70% {
                transform: scale(1);
                box-shadow: 0 0 0 10px rgba(76, 175, 80, 0);
            }
            100% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(76, 175, 80, 0);
            }
        }
        
        h1 {
            color: #333;
            margin-bottom: 30px;
            font-size: 2em;
        }
        
        .status-text {
            font-size: 24px;
            font-weight: bold;
            margin: 20px 0;
        }
        
        .online-text {
            color: #4CAF50;
        }
        
        .offline-text {
            color: #f44336;
        }
        
        .info {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 25px;
            margin: 25px 0;
            border-left: 4px solid #667eea;
        }
        
        .info-item {
            margin: 12px 0;
            color: #666;
            font-size: 16px;
        }
        
        .bot-icon {
            font-size: 80px;
            margin-bottom: 20px;
            filter: drop-shadow(0 4px 8px rgba(0,0,0,0.1));
        }
        
        .refresh-button {
            background: linear-gradient(45deg, #667eea, #764ba2);
            color: white;
            border: none;
            padding: 12px 25px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 16px;
            margin-top: 20px;
            transition: all 0.3s;
        }
        
        .refresh-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        
        .heartbeat-indicator {
            color: #e74c3c;
            font-size: 18px;
            animation: heartbeat 1.5s ease-in-out infinite;
        }
        
        @keyframes heartbeat {
            0% { transform: scale(1); }
            50% { transform: scale(1.1); }
            100% { transform: scale(1); }
        }
        
        .footer {
            margin-top: 30px;
            color: #888;
            font-size: 14px;
        }
    </style>
    <script>
        // Auto-refresh toutes les 30 secondes
        setTimeout(() => {
            location.reload();
        }, 30000);
        
        function refreshNow() {
            location.reload();
        }
        
        // Indicateur visuel du heartbeat
        function updateHeartbeat() {
            const heartbeat = document.querySelector('.heartbeat-indicator');
            if (heartbeat) {
                heartbeat.style.color = heartbeat.style.color === 'rgb(231, 76, 60)' ? '#e74c3c' : 'rgb(231, 76, 60)';
            }
        }
        
        setInterval(updateHeartbeat, 1000);
    </script>
</head>
<body>
    <div class="container">
        <div class="bot-icon">🤖</div>
        <h1>Bot Discord - Monitoring</h1>
        <div class="heartbeat-indicator">❤️ Heartbeat Actif</div>
        
        {% if status.online %}
            <div class="status-indicator online"></div>
            <div class="status-text online-text">✅ Bot en ligne et actif</div>
        {% else %}
            <div class="status-indicator offline"></div>
            <div class="status-text offline-text">❌ Bot temporairement hors ligne</div>
        {% endif %}
        
        <div class="info">
            {% if status.online %}
                <div class="info-item">
                    <strong>🤖 Nom du bot:</strong> {{ status.bot_name }}
                </div>
                <div class="info-item">
                    <strong>🚀 Démarré le:</strong> {{ status.start_time }}
                </div>
                <div class="info-item">
                    <strong>🏰 Serveurs connectés:</strong> {{ status.servers }}
                </div>
                {% if status.uptime %}
                    <div class="info-item">
                        <strong>⏰ Temps d'activité:</strong> {{ status.uptime }}
                    </div>
                {% endif %}
                {% if status.reconnect_count > 0 %}
                    <div class="info-item">
                        <strong>🔄 Reconnexions:</strong> {{ status.reconnect_count }}
                    </div>
                {% endif %}
            {% else %}
                <div class="info-item">
                    <strong>⚠️ Le bot Discord redémarre automatiquement...</strong>
                </div>
                <div class="info-item">
                    <strong>🔄 Système de heartbeat maintient l'activité</strong>
                </div>
            {% endif %}
            
            <div class="info-item">
                <strong>🔄 Dernière vérification:</strong> {{ status.last_update }}
            </div>
        </div>
        
        <button class="refresh-button" onclick="refreshNow()">
            🔄 Actualiser le statut
        </button>
        
        <div class="footer">
            <p>🔧 Optimisé pour Render | 📡 Heartbeat automatique</p>
        </div>
    </div>
</body>
</html>
        """

        def calculate_uptime(start_time):
            """Calculer le temps de fonctionnement"""
            if not start_time:
                return None
            
            now = datetime.now()
            uptime = now - start_time
            
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)
            
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"

        def update_bot_status():
            """Mettre à jour le statut du bot avec récupération automatique des erreurs fatales"""
            nonlocal bot_status
            global fatal_error_state, current_bot_instance, last_fatal_error_time
            
            try:
                # Lire l'état d'erreur fatale (lecture seule - le bot thread gère les clears)
                with fatal_error_lock:
                    current_fatal_state = fatal_error_state
                
                # Protéger toutes les modifications de bot_status avec le lock
                with bot_status_data_lock:
                    # Vérifier s'il y a une erreur fatale
                    if current_fatal_state:
                        if bot_status['online']:
                            bot_status['offline_since'] = datetime.now()
                            logger.warning("⚠️ Bot mis hors ligne à cause d'une erreur fatale")
                        bot_status['online'] = False
                        bot_status['bot_name'] = f'Bot Discord (Erreur fatale: {current_fatal_state})'
                        bot_status['servers'] = 0
                    else:
                        # Vérifier le statut du bot en utilisant la référence globale thread-safe
                        with bot_status_lock:
                            bot_instance = current_bot_instance
                        
                        if bot_instance and bot_instance.is_ready():
                            if not bot_status['online']:
                                now = datetime.now()
                                if not bot_status['first_online_time']:
                                    bot_status['first_online_time'] = now
                                bot_status['offline_since'] = None
                                logger.info("✅ Bot détecté comme en ligne")
                            
                            bot_status['online'] = True
                            bot_status['bot_name'] = str(bot_instance.user) if bot_instance.user else 'Bot Discord'
                            bot_status['servers'] = len(bot_instance.guilds) if bot_instance.guilds else 0
                        else:
                            if bot_status['online']:
                                bot_status['offline_since'] = datetime.now()
                                logger.warning("⚠️ Bot détecté comme hors ligne")
                                bot_status['reconnect_count'] += 1
                            elif not bot_status['offline_since']:
                                # Si jamais en ligne et offline_since pas défini
                                bot_status['offline_since'] = app_start_time
                                
                            bot_status['online'] = False
                            bot_status['bot_name'] = 'Bot Discord'
                            bot_status['servers'] = 0
                    
                    bot_status['last_update'] = datetime.now().strftime("%H:%M:%S")
                    
            except Exception as e:
                logger.error(f"Erreur lors de la mise à jour du statut: {e}")
                with bot_status_data_lock:
                    if bot_status['online']:
                        bot_status['offline_since'] = datetime.now()
                    bot_status['online'] = False

        @app.route('/')
        def status_page():
            """Page principale avec le statut du bot"""
            update_bot_status()
            
            # Lire bot_status de façon thread-safe
            with bot_status_data_lock:
                # Calculer le temps de fonctionnement
                uptime = calculate_uptime(bot_status['first_online_time'])
                status_with_uptime = bot_status.copy()
                status_with_uptime['uptime'] = uptime
                
                if status_with_uptime['first_online_time']:
                    status_with_uptime['start_time'] = status_with_uptime['first_online_time'].strftime("%d/%m/%Y à %H:%M:%S")
                else:
                    status_with_uptime['start_time'] = None
            
            return render_template_string(HTML_TEMPLATE, status=status_with_uptime)

        @app.route('/health')
        def health_check():
            """Endpoint de santé pour les services comme Render avec récupération d'erreurs"""
            update_bot_status()
            
            # Obtenir l'état d'erreur fatale de façon thread-safe
            with fatal_error_lock:
                current_fatal_state = fatal_error_state
                error_time = last_fatal_error_time
            
            # Grace period for startup (2 minutes)
            grace_period = 120
            now = datetime.now()
            
            # Calculer le temps depuis le démarrage de l'app
            app_runtime = (now - app_start_time).total_seconds()
            
            # Lire bot_status de façon thread-safe
            with bot_status_data_lock:
                bot_online = bot_status['online']
                offline_since = bot_status['offline_since']
            
            # Calculer la durée hors ligne
            if offline_since:
                offline_duration = (now - offline_since).total_seconds()
            else:
                offline_duration = 0
            
            # Vérifier s'il y a une erreur fatale
            if current_fatal_state:
                return jsonify({
                    'status': 'unhealthy',
                    'bot_online': False,
                    'timestamp': now.isoformat(),
                    'error': f'Fatal error: {current_fatal_state}',
                    'app_runtime': app_runtime,
                    'fatal': True,
                    'error_time': error_time.isoformat() if error_time else None
                }), 503
            
            # Retourner 503 si le bot est hors ligne au-delà de la période de grâce
            if not bot_online and offline_duration > grace_period:
                return jsonify({
                    'status': 'unhealthy',
                    'bot_online': False,
                    'timestamp': now.isoformat(),
                    'error': f'Bot offline for {offline_duration:.0f}s (grace: {grace_period}s)',
                    'offline_duration': offline_duration,
                    'app_runtime': app_runtime
                }), 503
            
            # Retourner 200 si le bot est en ligne ou dans la période de grâce
            return jsonify({
                'status': 'healthy',
                'bot_online': bot_online,
                'timestamp': now.isoformat(),
                'heartbeat': True,
                'offline_duration': offline_duration,
                'app_runtime': app_runtime,
                'service': 'discord-bot-heartbeat'
            }), 200

        @app.route('/api/status')
        def api_status():
            """API JSON pour le statut du bot"""
            update_bot_status()
            with bot_status_data_lock:
                return jsonify(bot_status.copy())
        
        @app.route('/ready')
        def readiness_check():
            """Endpoint de préparation - vérifie si le service est prêt"""
            update_bot_status()
            with bot_status_data_lock:
                bot_online = bot_status['online']
            
            if bot_online:
                return jsonify({
                    'status': 'ready',
                    'bot_online': True,
                    'timestamp': datetime.now().isoformat()
                }), 200
            else:
                return jsonify({
                    'status': 'not_ready',
                    'bot_online': False,
                    'timestamp': datetime.now().isoformat()
                }), 503

        @app.route('/ping')
        def ping():
            """Endpoint simple pour vérifier que le service répond"""
            return jsonify({
                'message': 'pong',
                'timestamp': datetime.now().isoformat(),
                'service': 'active'
            }), 200

        # Configuration pour éviter le cache
        @app.after_request
        def after_request(response):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        
        # Démarrer le serveur web avec Waitress (production WSGI)
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"🌐 Démarrage du serveur web de production sur le port {port}...")
        
        try:
            from waitress import serve
            serve(app, host='0.0.0.0', port=port, threads=4, cleanup_interval=30, 
                 connection_limit=1000, channel_timeout=120)
        except ImportError:
            logger.warning("⚠️ Waitress non disponible, utilisation du serveur de développement Flask")
            app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
        
    except Exception as e:
        logger.error(f"❌ Erreur lors du démarrage du serveur web: {e}")
        sys.exit(1)

def main():
    """Fonction principale qui lance les deux services"""
    logger.info("🚀 Démarrage de l'application complète avec système de heartbeat...")
    
    try:
        # Lancer le bot Discord dans un thread séparé
        discord_thread = threading.Thread(target=run_discord_bot, daemon=True)
        discord_thread.start()
        logger.info("📱 Thread Discord lancé")
        
        # Attendre un peu pour que le bot se lance
        time.sleep(3)
        
        # Lancer le serveur web (bloquant)
        logger.info("🌐 Lancement du serveur web Flask...")
        run_web_server()
        
    except KeyboardInterrupt:
        logger.info("🔴 Arrêt demandé par l'utilisateur")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"❌ Erreur critique dans l'application principale: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
