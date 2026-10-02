package com.example.mpvremote

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.KeyEvent
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.activity.addCallback
import androidx.appcompat.app.ActionBarDrawerToggle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.edit
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
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
    private lateinit var openLinkSettingsButton: Button
    private lateinit var controlPlayPause: ImageButton
    private lateinit var controlPrevious: ImageButton
    private lateinit var controlNext: ImageButton
    private lateinit var controlStop: ImageButton
    private lateinit var controlMute: ImageButton
    private lateinit var controlClear: Button
    private lateinit var sendClipboardButton: Button

    private lateinit var playbackProgress: com.google.android.material.slider.Slider
    private lateinit var playbackTime: TextView

    private lateinit var playlistSwipeRefresh: SwipeRefreshLayout
    private lateinit var playlistRecyclerView: RecyclerView
    private lateinit var playlistEmptyView: TextView
    private lateinit var playlistAdapter: PlaylistAdapter
    private val titleResolver = TitleResolver()

    private val pollHandler = Handler(Looper.getMainLooper())
    private var isPolling = false
    private var isActivityResumed = false
    private var isUserSeeking = false
    private var lastKnownPlaylistPos = -1
    private var lastKnownDuration = 0.0

    private var lastVolumeUpTime = 0L
    private var lastVolumeDownTime = 0L
    private var isMuted = false

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
        openLinkSettingsButton = findViewById(R.id.open_link_settings_button)
        controlPlayPause = findViewById(R.id.control_play_pause)
        controlPrevious = findViewById(R.id.control_previous)
        controlNext = findViewById(R.id.control_next)
        controlStop = findViewById(R.id.control_stop)
        controlMute = findViewById(R.id.control_mute)
        controlClear = findViewById(R.id.control_clear)
        sendClipboardButton = findViewById(R.id.send_clipboard_button)

        playbackProgress = findViewById(R.id.playback_progress)
        playbackTime = findViewById(R.id.playback_time)

        playlistSwipeRefresh = findViewById(R.id.playlist_swipe_refresh)
        playlistRecyclerView = findViewById(R.id.playlist_recycler_view)
        playlistEmptyView = findViewById(R.id.playlist_empty_view)

        setupDrawer()
        setupPlaylist()
        setupPlaybackProgress()
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

        // Only handle a SHARE/VIEW intent on a fresh creation. After a configuration
        // change (rotation, theme switch, etc.) Android recreates the Activity
        // with the same intent, so re-processing it would resend the same URL.
        if (savedInstanceState == null) {
            when (intent.action) {
                Intent.ACTION_SEND -> if (intent.type == "text/plain") handleSendIntent(intent)
                Intent.ACTION_VIEW -> handleViewIntent(intent)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        when (intent.action) {
            Intent.ACTION_SEND -> if (intent.type == "text/plain") {
                showSection(Section.REMOTE)
                handleSendIntent(intent)
            }
            Intent.ACTION_VIEW -> {
                showSection(Section.REMOTE)
                handleViewIntent(intent)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        isActivityResumed = true
        if (remoteSection.visibility == View.VISIBLE) {
            startStatePolling()
        }
    }

    override fun onPause() {
        super.onPause()
        isActivityResumed = false
        stopStatePolling()
    }

    override fun onDestroy() {
        super.onDestroy()
        stopStatePolling()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        return when (event.keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> {
                if (event.action == KeyEvent.ACTION_DOWN) {
                    handleVolumeEvent(ACTION_VOLUME_UP) { lastVolumeUpTime = it }
                }
                true
            }
            KeyEvent.KEYCODE_VOLUME_DOWN -> {
                if (event.action == KeyEvent.ACTION_DOWN) {
                    handleVolumeEvent(ACTION_VOLUME_DOWN) { lastVolumeDownTime = it }
                }
                true
            }
            else -> super.dispatchKeyEvent(event)
        }
    }

    private fun handleVolumeEvent(action: String, updateLastTime: (Long) -> Unit) {
        val now = SystemClock.elapsedRealtime()
        if (now - (if (action == ACTION_VOLUME_UP) lastVolumeUpTime else lastVolumeDownTime) < VOLUME_THROTTLE_MS) {
            return
        }
        updateLastTime(now)
        sendControlRequest(action, silentSuccess = true, refreshPlaylist = false)
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

    private fun setupPlaylist() {
        playlistAdapter = PlaylistAdapter(
            titleResolver = titleResolver,
            onItemClick = { item -> playPlaylistItem(item) },
            onRemove = { item -> removePlaylistItem(item) }
        )
        playlistRecyclerView.layoutManager = LinearLayoutManager(this)
        playlistRecyclerView.adapter = playlistAdapter
        playlistSwipeRefresh.setOnRefreshListener { loadPlaylist() }
    }

    private fun setupListeners() {
        saveButton.setOnClickListener { saveSettings() }
        controlPlayPause.setOnClickListener { sendControlRequest(ACTION_PLAY_PAUSE) }
        controlPrevious.setOnClickListener { sendControlRequest(ACTION_PREVIOUS) }
        controlNext.setOnClickListener { sendControlRequest(ACTION_NEXT) }
        controlStop.setOnClickListener { sendControlRequest(ACTION_STOP) }
        controlMute.setOnClickListener { toggleMute() }
        controlClear.setOnClickListener { sendControlRequest(ACTION_CLEAR) }
        sendClipboardButton.setOnClickListener { sendClipboardUrl() }
        openLinkSettingsButton.setOnClickListener { openLinkSettings() }
    }

    private fun showSection(section: Section) {
        when (section) {
            Section.REMOTE -> {
                remoteSection.visibility = View.VISIBLE
                settingsSection.visibility = View.GONE
                supportActionBar?.title = getString(R.string.nav_remote)
                navigationView.setCheckedItem(R.id.nav_remote)
                loadPlaylist()
                if (isActivityResumed) {
                    startStatePolling()
                }
            }
            Section.SETTINGS -> {
                remoteSection.visibility = View.GONE
                settingsSection.visibility = View.VISIBLE
                supportActionBar?.title = getString(R.string.nav_settings)
                navigationView.setCheckedItem(R.id.nav_settings)
                stopStatePolling()
            }
        }
    }

    private fun setupPlaybackProgress() {
        playbackProgress.addOnSliderTouchListener(object : com.google.android.material.slider.Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: com.google.android.material.slider.Slider) {
                isUserSeeking = true
            }

            override fun onStopTrackingTouch(slider: com.google.android.material.slider.Slider) {
                isUserSeeking = false
                val position = slider.value.toDouble()
                if (lastKnownDuration > 0 && position >= 0) {
                    seekTo(position)
                }
            }
        })

        playbackProgress.addOnChangeListener { _, value, fromUser ->
            if (fromUser && lastKnownDuration > 0) {
                playbackTime.text = getString(
                    R.string.playback_time_format,
                    formatTime(value.toDouble()),
                    formatTime(lastKnownDuration)
                )
            }
        }
    }

    private fun seekTo(position: Double) {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", ACTION_SEEK)
                    put("position", position)
                    put("socket", settings.socketPath)
                }
                postJson(settings.baseUrl, "/control", payload)
                runOnUiThread { fetchStateImmediate() }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun startStatePolling() {
        if (isPolling) return
        isPolling = true
        scheduleStatePoll(0)
    }

    private fun stopStatePolling() {
        isPolling = false
        pollHandler.removeCallbacksAndMessages(null)
    }

    private fun scheduleStatePoll(delayMs: Long) {
        if (!isPolling) return
        pollHandler.postDelayed({ fetchState() }, delayMs)
    }

    private fun fetchStateImmediate() {
        if (remoteSection.visibility != View.VISIBLE) return
        pollHandler.removeCallbacksAndMessages(null)
        fetchState()
    }

    private fun fetchState() {
        if (!isPolling || remoteSection.visibility != View.VISIBLE) return

        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            scheduleStatePoll(STATE_POLL_INTERVAL_MS)
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("socket", settings.socketPath)
                }
                val body = postJsonForResponse(settings.baseUrl, "/state", payload)
                val response = JSONObject(body)
                runOnUiThread {
                    updateStateUI(response)
                    val idleActive = response.optBoolean("idle_active", true)
                    if (!idleActive && isPolling) {
                        scheduleStatePoll(STATE_POLL_INTERVAL_MS)
                    } else {
                        isPolling = false
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (isPolling) {
                        scheduleStatePoll(STATE_POLL_INTERVAL_MS)
                    }
                }
            }
        }.start()
    }

    private fun updateStateUI(response: JSONObject) {
        val newPos = response.optInt("playlist_pos", -1)
        if (newPos != -1 && newPos != lastKnownPlaylistPos) {
            lastKnownPlaylistPos = newPos
            loadPlaylist()
        }
        updateProgressUI(response)
    }

    private fun updateProgressUI(response: JSONObject) {
        val timePosObj = response.takeIf { it.has("time_pos") && !it.isNull("time_pos") }
        val durationObj = response.takeIf { it.has("duration") && !it.isNull("duration") }

        if (timePosObj != null && durationObj != null) {
            val timePos = timePosObj.optDouble("time_pos", 0.0)
            val duration = durationObj.optDouble("duration", 0.0)
            lastKnownDuration = duration
            if (duration > 0) {
                playbackProgress.valueFrom = 0f
                playbackProgress.valueTo = duration.toFloat()
                playbackProgress.isEnabled = true
                if (!isUserSeeking) {
                    playbackProgress.value = timePos.toFloat().coerceIn(0f, duration.toFloat())
                }
                playbackTime.text = getString(
                    R.string.playback_time_format,
                    formatTime(timePos),
                    formatTime(duration)
                )
                return
            }
        }

        lastKnownDuration = 0.0
        playbackProgress.isEnabled = false
        playbackProgress.valueFrom = 0f
        playbackProgress.valueTo = 100f
        playbackProgress.value = 0f
        playbackTime.text = getString(R.string.playback_time_unknown)
    }

    private fun formatTime(seconds: Double): String {
        val total = seconds.toInt()
        val hours = total / 3600
        val minutes = (total % 3600) / 60
        val secs = total % 60
        return if (hours > 0) {
            String.format("%d:%02d:%02d", hours, minutes, secs)
        } else {
            String.format("%02d:%02d", minutes, secs)
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

    private fun openLinkSettings() {
        try {
            val intent = Intent(
                "android.settings.APP_OPEN_BY_DEFAULT_SETTINGS",
                Uri.parse("package:$packageName")
            )
            startActivity(intent)
        } catch (e: Exception) {
            showError(getString(R.string.open_link_settings_error))
        }
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
        sendUrl(sharedUrl)
    }

    private fun sendClipboardUrl() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = clipboard.primaryClip
        if (clip == null || clip.itemCount == 0) {
            showError(getString(R.string.clipboard_empty))
            return
        }
        val text = clip.getItemAt(0).text?.toString()
        if (text.isNullOrBlank()) {
            showError(getString(R.string.clipboard_empty))
            return
        }
        if (!isValidHttpUrl(text)) {
            showError(getString(R.string.clipboard_no_url))
            return
        }
        sendUrl(text)
    }

    private fun handleViewIntent(intent: Intent) {
        val data: Uri = intent.data ?: return
        val url = data.toString()
        if (!isValidHttpUrl(url)) {
            showError(getString(R.string.error_send, getString(R.string.error_no_url)))
            return
        }
        sendUrl(url)
    }

    private fun sendUrl(url: String) {
        val trimmed = url.trim()
        if (!isValidHttpUrl(trimmed)) {
            showError(getString(R.string.error_send, getString(R.string.error_no_url)))
            return
        }

        val settings = currentSettings()

        Thread {
            try {
                sendPlayRequest(settings.baseUrl, settings.socketPath, settings.append, trimmed)
                runOnUiThread {
                    showSuccess(getString(R.string.success_sent, trimmed))
                    loadPlaylist()
                    fetchStateImmediate()
                }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun isValidHttpUrl(url: String): Boolean {
        return url.startsWith("http://", ignoreCase = true) ||
                url.startsWith("https://", ignoreCase = true)
    }

    private fun sendControlRequest(
        action: String,
        silentSuccess: Boolean = false,
        refreshPlaylist: Boolean = true
    ) {
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
                runOnUiThread {
                    if (!silentSuccess) {
                        showSuccess(getString(R.string.success_control, action))
                    }
                    if (refreshPlaylist) {
                        loadPlaylist()
                    }
                    fetchStateImmediate()
                }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun toggleMute() {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", ACTION_TOGGLE_MUTE)
                    put("socket", settings.socketPath)
                }
                val body = postJsonForResponse(settings.baseUrl, "/control", payload)
                val response = JSONObject(body)
                val muted = if (response.has("muted")) {
                    response.getBoolean("muted")
                } else {
                    !isMuted
                }
                runOnUiThread {
                    isMuted = muted
                    updateMuteIcon()
                    showSuccess(getString(if (isMuted) R.string.mute_enabled else R.string.mute_disabled))
                }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun updateMuteIcon() {
        controlMute.setImageResource(if (isMuted) R.drawable.ic_volume_off else R.drawable.ic_volume_on)
    }

    private fun playPlaylistItem(item: PlaylistItem) {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", ACTION_PLAY_INDEX)
                    put("index", item.index)
                    put("socket", settings.socketPath)
                }
                postJson(settings.baseUrl, "/control", payload)
                runOnUiThread {
                    showSuccess(getString(R.string.success_control, ACTION_PLAY_INDEX))
                    loadPlaylist()
                    fetchStateImmediate()
                }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun removePlaylistItem(item: PlaylistItem) {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            showError(getString(R.string.error_send, getString(R.string.error_empty_relay)))
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("action", ACTION_REMOVE_INDEX)
                    put("index", item.index)
                    put("socket", settings.socketPath)
                }
                postJson(settings.baseUrl, "/control", payload)
                runOnUiThread {
                    showSuccess(getString(R.string.playlist_removed))
                    loadPlaylist()
                    fetchStateImmediate()
                }
            } catch (e: Exception) {
                runOnUiThread { showError(e.message ?: e.javaClass.simpleName) }
            }
        }.start()
    }

    private fun loadPlaylist() {
        val settings = currentSettings()
        if (settings.baseUrl.isBlank()) {
            runOnUiThread {
                playlistSwipeRefresh.isRefreshing = false
                playlistAdapter.submitList(emptyList())
                playlistEmptyView.visibility = View.VISIBLE
            }
            return
        }

        Thread {
            try {
                val payload = JSONObject().apply {
                    put("socket", settings.socketPath)
                }
                val body = postJsonForResponse(settings.baseUrl, "/playlist", payload)
                val response = JSONObject(body)
                val items = parsePlaylist(response)
                runOnUiThread {
                    playlistAdapter.submitList(items)
                    playlistEmptyView.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
                    playlistSwipeRefresh.isRefreshing = false
                }
            } catch (e: Exception) {
                runOnUiThread {
                    playlistSwipeRefresh.isRefreshing = false
                    showError(getString(R.string.error_playlist, e.message ?: e.javaClass.simpleName))
                }
            }
        }.start()
    }

    private fun parsePlaylist(response: JSONObject): List<PlaylistItem> {
        val items = response.optJSONArray("items") ?: return emptyList()
        val result = mutableListOf<PlaylistItem>()
        for (i in 0 until items.length()) {
            val obj = items.getJSONObject(i)
            result.add(
                PlaylistItem(
                    index = obj.optInt("index", i),
                    title = obj.optString("title", ""),
                    filename = obj.optString("filename", ""),
                    current = obj.optBoolean("current", false),
                    playing = obj.optBoolean("playing", false)
                )
            )
        }
        return result
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
        postJsonForResponse(baseUrl, endpointSuffix, payload)
    }

    private fun postJsonForResponse(
        baseUrl: String,
        endpointSuffix: String,
        payload: JSONObject
    ): String {
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
            val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (responseCode !in 200..299) {
                throw RuntimeException("HTTP $responseCode${if (body.isNotBlank()) ": $body" else ""}")
            }
            return body
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
        private const val ACTION_PLAY_INDEX = "play_index"
        private const val ACTION_REMOVE_INDEX = "remove_index"
        private const val ACTION_VOLUME_UP = "volume_up"
        private const val ACTION_VOLUME_DOWN = "volume_down"
        private const val ACTION_TOGGLE_MUTE = "toggle_mute"
        private const val ACTION_SEEK = "seek"

        private const val VOLUME_THROTTLE_MS = 120L
        private const val STATE_POLL_INTERVAL_MS = 1000L
    }
}
