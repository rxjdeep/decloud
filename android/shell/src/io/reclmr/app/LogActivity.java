package io.reclmr.app;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** The app's log, selectable, with copy and clear. */
public class LogActivity extends Activity {
    private TextView text;
    private ScrollView scroll;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        float d = getResources().getDisplayMetrics().density;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding((int) (12 * d), (int) (28 * d), (int) (12 * d), (int) (12 * d));
        LinearLayout row = new LinearLayout(this);
        row.addView(button("Copy log", new View.OnClickListener() { @Override public void onClick(View v) {
            ((ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("reclmr log", Log.tail(LogActivity.this, 400_000)));
            Toast.makeText(LogActivity.this, "Log copied", Toast.LENGTH_SHORT).show();
        } }));
        row.addView(button("Clear", new View.OnClickListener() { @Override public void onClick(View v) { Log.clear(LogActivity.this); show(); } }));
        row.addView(button("Refresh", new View.OnClickListener() { @Override public void onClick(View v) { show(); } }));
        root.addView(row);
        scroll = new ScrollView(this);
        text = new TextView(this);
        text.setTextSize(10.5f);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextIsSelectable(true);
        scroll.addView(text);
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
    }

    private Button button(String t, View.OnClickListener l) {
        Button x = new Button(this);
        x.setText(t); x.setAllCaps(false); x.setOnClickListener(l);
        x.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return x;
    }

    @Override protected void onResume() { super.onResume(); show(); }

    private void show() {
        String s = Log.tail(this, 80_000);
        text.setText(s.isEmpty() ? "(the log is empty)" : s);
        scroll.post(new Runnable() { @Override public void run() { scroll.fullScroll(View.FOCUS_DOWN); } });
    }
}
