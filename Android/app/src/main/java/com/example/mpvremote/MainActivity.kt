package com.example.mpvremote

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.edit
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var baseUrlInput: EditText
    private lateinit var socketPathInput: EditText
    private lateinit var appendCheckbox: CheckBox
    private lateinit var saveButton: Button
    private lateinit var controlPlayPause: ImageButton
    private lateinit var controlPrevious: ImageButton
    private lateinit var controlNext: ImageButton
    private lateinit var controlStop: ImageButton
    private lateinit var controlClear: Button
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        baseUrlInput = findViewById(R.id.base_url_input)
        socketPathInput = findViewById(R.id.socket_path_input)
        appendCheckbox = findViewById(R.id.append_checkbox)
        saveButton = findViewById(R.id.save_button)
        controlPlayPause = findViewById(R.id.control_play_pause)
        controlPrevious = findViewById(R.id.control_previous)
        controlNext = findViewById(R.id.control_next)
        controlStop = findViewById(R.id.control_stop)
        controlClear = findViewById(R.id.control_clear)
        statusText = findViewById(R.id.status_text)

        loadSettings()

        saveButton.setOnClickListener { saveSettings() }
        controlPlayPause.setOnClickListener { sendControlRequest(ACTION_PLAY_PAUSE) }
        controlPrevious.setOnClickListener { sendControlRequest(ACTION_PREVIOUS) }
        controlNext.setOnClickListener { sendControlRequest(ACTION_NEXT) }
        controlStop.setOnClickListener { sendControlRequest(ACTION_STOP) }
        controlClear.setOnClickListener { sendControlRequest(ACTION_CLEAR) }

        if (intent.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            handleSendIntent(intent)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            handleSendIntent(intent)
        }
    }

    private fun loadSettings() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        baseUrlInput.setText(prefs.getString(KEY_BASE_URL, DEFAULT_BASE_URL))
        socketPathInput.setText(prefs.getString(KEY_SOCKET, DEFAULT_SOCKET))
        appendCheckbox.isChecked = prefs.getBoolean(KEY_APPEND, DEFAULT_APPEND)
    }

    private fun saveSettings() {
        val baseUrl = baseUrlInput.text.toString().trim()
        val socketPath = socketPathInput.text.toString().trim()
        val append = appendCheckbox.isChecked

        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit {
            putString(KEY_BASE_URL, baseUrl)
            putString(KEY_SOCKET, socketPath)
            putBoolean(KEY_APPEND, append)
        }

        Toast.makeText(this, R.string.settings_saved, Toast.LENGTH_SHORT).show()
    }

    private fun handleSendIntent(intent: Intent) {
        val sharedUrl = intent.getStringExtra(Intent.EXTRA_TEXT)
        if (sharedUrl.isNullOrBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_no_url)))
            return
        }

        val baseUrl = baseUrlInput.text.toString().trim()
        val socketPath = socketPathInput.text.toString().trim()
        val append = appendCheckbox.isChecked

        setStatusSending(getString(R.string.sending, sharedUrl))

        Thread {
            try {
                sendPlayRequest(baseUrl, socketPath, append, sharedUrl)
                runOnUiThread { setStatusSuccess(getString(R.string.success_sent, sharedUrl)) }
            } catch (e: Exception) {
                runOnUiThread { setStatusError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun sendControlRequest(action: String) {
        val baseUrl = baseUrlInput.text.toString().trim()
        if (baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        val socketPath = socketPathInput.text.toString().trim()

        setStatusSending(getString(R.string.sending_control, action))

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", action)
                    put("socket", socketPath)
                }
                postJson(baseUrl, "/control", payload)
                runOnUiThread { setStatusSuccess(getString(R.string.success_control, action)) }
            } catch (e: Exception) {
                runOnUiThread { setStatusError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun sendPlayRequest(
        baseUrl: String,
        socketPath: String,
        append: Boolean,
        url: String
    ) {
        if (baseUrl.isBlank()) {
            throw IllegalArgumentException(getString(R.string.error_empty_relay))
        }

        val payload = JSONObject().apply {
            put("url", url)
            put("socket", socketPath)
            put("append", append)
        }

        postJson(baseUrl, "/play", payload)
    }

    private fun postJson(baseUrl: String, endpointSuffix: String, payload: JSONObject) {
        val endpoint = baseUrl.trimEnd('/') + endpointSuffix
        val connection = URL(endpoint).openConnection() as HttpURLConnection

        try {
            connection.requestMethod = "POST"
            connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
            connection.doOutput = true
            connection.connectTimeout = 5000
            connection.readTimeout = 5000

            OutputStreamWriter(connection.outputStream).use { writer ->
                writer.write(payload.toString())
                writer.flush()
            }

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) {
                val errorBody = connection.errorStream?.bufferedReader()?.use { it.readText() } ?: ""
                throw RuntimeException("HTTP $responseCode${if (errorBody.isNotBlank()) ": $errorBody" else ""}")
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun setStatusSending(message: String) {
        statusText.text = message
        statusText.setTextColor(getColor(R.color.text_primary))
    }

    private fun setStatusSuccess(message: String) {
        statusText.text = message
        statusText.setTextColor(getColor(R.color.success))
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun setStatusError(message: String) {
        statusText.text = getString(R.string.error_send, message)
        statusText.setTextColor(getColor(R.color.error))
        Toast.makeText(this, getString(R.string.error_send, message), Toast.LENGTH_LONG).show()
    }

    private fun showError(message: String) {
        statusText.text = message
        statusText.setTextColor(getColor(R.color.error))
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    companion object {
        private const val PREFS_NAME = "MpvRemotePrefs"
        private const val KEY_BASE_URL = "relay_base_url"
        private const val KEY_SOCKET = "mpv_socket_path"
        private const val KEY_APPEND = "mpv_append_mode"
        private const val DEFAULT_BASE_URL = "http://10.0.2.2:8765"
        private const val DEFAULT_SOCKET = "/tmp/mpv.socket"
        private const val DEFAULT_APPEND = false

        private const val ACTION_PLAY_PAUSE = "toggle_pause"
        private const val ACTION_PREVIOUS = "previous"
        private const val ACTION_NEXT = "next"
        private const val ACTION_STOP = "stop"
        private const val ACTION_CLEAR = "clear"
    }
}
