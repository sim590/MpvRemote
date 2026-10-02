package com.example.mpvremote

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageButton
import android.widget.Toast
import androidx.activity.addCallback
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.edit
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.navigation.NavigationView
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var drawerLayout: DrawerLayout
    private lateinit var toolbar: MaterialToolbar
    private lateinit var navigationView: NavigationView
    private lateinit var remoteSection: View
    private lateinit var settingsSection: View

    private lateinit var baseUrlInput: EditText
    private lateinit var socketPathInput: EditText
    private lateinit var appendCheckbox: CheckBox
    private lateinit var saveButton: Button
    private lateinit var controlPlayPause: ImageButton
    private lateinit var controlPrevious: ImageButton
    private lateinit var controlNext: ImageButton
    private lateinit var controlStop: ImageButton
    private lateinit var controlClear: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        drawerLayout = findViewById(R.id.drawer_layout)
        toolbar = findViewById(R.id.toolbar)
        navigationView = findViewById(R.id.navigation_view)
        remoteSection = findViewById(R.id.remote_section)
        settingsSection = findViewById(R.id.settings_section)

        baseUrlInput = findViewById(R.id.base_url_input)
        socketPathInput = findViewById(R.id.socket_path_input)
        appendCheckbox = findViewById(R.id.append_checkbox)
        saveButton = findViewById(R.id.save_button)
        controlPlayPause = findViewById(R.id.control_play_pause)
        controlPrevious = findViewById(R.id.control_previous)
        controlNext = findViewById(R.id.control_next)
        controlStop = findViewById(R.id.control_stop)
        controlClear = findViewById(R.id.control_clear)

        setupDrawer()
        showSection(Section.REMOTE)
        loadSettings()
        setupListeners()

        onBackPressedDispatcher.addCallback(this) {
            if (drawerLayout.isDrawerOpen(GravityCompat.START)) {
                drawerLayout.closeDrawer(GravityCompat.START)
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }

        if (intent.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            handleSendIntent(intent)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            showSection(Section.REMOTE)
            handleSendIntent(intent)
        }
    }

    private fun setupDrawer() {
        setSupportActionBar(toolbar)
        val toggle = ActionBarDrawerToggle(
            this,
            drawerLayout,
            toolbar,
            R.string.drawer_open,
            R.string.drawer_close
        )
        drawerLayout.addDrawerListener(toggle)
        toggle.syncState()

        navigationView.setNavigationItemSelectedListener { menuItem ->
            when (menuItem.itemId) {
                R.id.nav_remote -> showSection(Section.REMOTE)
                R.id.nav_settings -> showSection(Section.SETTINGS)
            }
            drawerLayout.closeDrawer(GravityCompat.START)
            true
        }
    }

    private fun setupListeners() {
        saveButton.setOnClickListener { saveSettings() }
        controlPlayPause.setOnClickListener { sendControlRequest(ACTION_PLAY_PAUSE) }
        controlPrevious.setOnClickListener { sendControlRequest(ACTION_PREVIOUS) }
        controlNext.setOnClickListener { sendControlRequest(ACTION_NEXT) }
        controlStop.setOnClickListener { sendControlRequest(ACTION_STOP) }
        controlClear.setOnClickListener { sendControlRequest(ACTION_CLEAR) }
    }

    private fun showSection(section: Section) {
        when (section) {
            Section.REMOTE -> {
                remoteSection.visibility = View.VISIBLE
                settingsSection.visibility = View.GONE
                supportActionBar?.title = getString(R.string.nav_remote)
                navigationView.setCheckedItem(R.id.nav_remote)
            }
            Section.SETTINGS -> {
                remoteSection.visibility = View.GONE
                settingsSection.visibility = View.VISIBLE
                supportActionBar?.title = getString(R.string.nav_settings)
                navigationView.setCheckedItem(R.id.nav_settings)
            }
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

    private fun currentSettings(): Settings {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        return Settings(
            baseUrl = prefs.getString(KEY_BASE_URL, DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL,
            socketPath = prefs.getString(KEY_SOCKET, DEFAULT_SOCKET) ?: DEFAULT_SOCKET,
            append = prefs.getBoolean(KEY_APPEND, DEFAULT_APPEND)
        )
    }

    private fun handleSendIntent(intent: Intent) {
        val sharedUrl = intent.getStringExtra(Intent.EXTRA_TEXT)
        if (sharedUrl.isNullOrBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_no_url)))
            return
        }

        val settings = currentSettings()

        Thread {
            try {
                sendPlayRequest(settings.baseUrl, settings.socketPath, settings.append, sharedUrl)
                runOnUiThread { showSuccess(getString(R.string.success_sent, sharedUrl)) }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun sendControlRequest(action: String) {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", action)
                    put("socket", settings.socketPath)
                }
                postJson(settings.baseUrl, "/control", payload)
                runOnUiThread { showSuccess(getString(R.string.success_control, action)) }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
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

    private fun showSuccess(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun showError(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    private enum class Section {
        REMOTE,
        SETTINGS
    }

    private data class Settings(
        val baseUrl: String,
        val socketPath: String,
        val append: Boolean
    )

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
