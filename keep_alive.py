from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/health')
def home():
    return "Bot Discord en ligne !"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()
